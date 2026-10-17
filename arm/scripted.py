"""Scripted pick: the fallback when a learned policy is not ready or fails at the venue.

Uses the SO-101 model (arm/so101/so101_new_calib.urdf, from TheRobotStudio/SO-ARM100)
with ikpy for inverse kinematics. Joint angles follow LeRobot's degree convention
(0 deg = middle of the calibrated range), which is what the "new_calib" model uses.

Two steps:
  1. table calibration (once per board placement): the arm goes limp, you touch the
     gripper tip to known points - the table markers and the object spots - skipping
     any it can't reach (at least 3); forward kinematics gives where the tip is in
     robot coordinates, and a 2D rigid fit maps table mm -> robot metres.
         scripted.cmd calibrate
  2. pick an object from gaze/objects.json. With a "drop" spot in that file it is
     carried there and released; without one it is HANDED OVER: lifted high above its
     spot, held for a few seconds so the user can take it, then released.
         scripted.cmd pick granola      (or via the arm runner with {"type": "scripted"})
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from arm.motion import glide, glide_time, hold, open_bus, read

ARM_DIR = Path(__file__).parent
URDF = ARM_DIR / "so101" / "so101_new_calib.urdf"
TABLE_CALIB = ARM_DIR / "table_to_robot.json"
OBJECTS = ARM_DIR.parent / "gaze" / "objects.json"
ROBOT_CALIB = Path.home() / ".cache" / "huggingface" / "lerobot" / "calibration" / "robots" / "so_follower" / "my_follower.json"
ARM_JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
TICKS_PER_DEG = 4095 / 360

HOVER_M = 0.08  # approach height above the table
GRASP_M = 0.015  # gripper tip height above the table when closing
GRIPPER_OPEN = 70.0  # % of the calibrated gripper range
GRIPPER_CLOSED = 5.0
HANDOVER_M = 0.18  # hand-over height above the table (no drop spot)
HANDOVER_S = 3.0  # how long to hold it up before letting go


# ---------- raw <-> degrees (same convention as LeRobot's MotorNormMode.DEGREES) ----------

def load_robot_calibration() -> dict[str, dict]:
    return json.loads(ROBOT_CALIB.read_text())


def to_deg(raw: dict[str, int], cal: dict[str, dict]) -> dict[str, float]:
    return {j: (raw[j] - (cal[j]["range_min"] + cal[j]["range_max"]) / 2) / TICKS_PER_DEG for j in ARM_JOINTS}


def to_raw(deg: dict[str, float], cal: dict[str, dict]) -> dict[str, int]:
    out = {}
    for j, d in deg.items():
        lo, hi = cal[j]["range_min"], cal[j]["range_max"]
        out[j] = int(round(min(max(d * TICKS_PER_DEG + (lo + hi) / 2, lo + 20), hi - 20)))  # stay inside the range
    return out


def gripper_raw(percent: float, cal: dict[str, dict]) -> int:
    lo, hi = cal["gripper"]["range_min"], cal["gripper"]["range_max"]
    return int(lo + (hi - lo) * percent / 100)


# ---------- kinematics ----------

class Kinematics:
    def __init__(self):
        from ikpy.chain import Chain  # imported here so the rest of the arm tools work without ikpy

        chain = Chain.from_urdf_file(str(URDF), base_elements=["base_link"])
        mask = [link.name in ARM_JOINTS for link in chain.links]
        self.chain = Chain.from_urdf_file(str(URDF), base_elements=["base_link"], active_links_mask=mask)
        self.names = [link.name for link in self.chain.links]
        self.index = {j: self.names.index(j) for j in ARM_JOINTS}
        self.down_axis = self._approach_axis()

    def _vector(self, deg: dict[str, float]) -> np.ndarray:
        q = np.zeros(len(self.names))
        for j, i in self.index.items():
            q[i] = math.radians(deg.get(j, 0.0))
        return q

    def fk(self, deg: dict[str, float]) -> np.ndarray:
        """4x4 pose of the gripper tip frame in the robot base frame (metres)."""
        return self.chain.forward_kinematics(self._vector(deg))

    def _approach_axis(self) -> str:
        """Which axis of the tip frame points out of the gripper (towards the fingertips)."""
        tip = self.fk({})
        wrist_i = self.index["wrist_flex"]
        frames = self.chain.forward_kinematics(self._vector({}), full_kinematics=True)
        out = tip[:3, 3] - frames[wrist_i][:3, 3]
        out /= np.linalg.norm(out)
        scores = {axis: abs(float(tip[:3, k] @ out)) for k, axis in enumerate("XYZ")}
        return max(scores, key=scores.get)

    def ik(self, xyz, seed: dict[str, float], point_down: bool = True) -> dict[str, float]:
        """Joint degrees putting the tip at xyz (metres), gripper pointing down if possible."""
        q0 = self._vector(seed)
        if point_down:
            q = self.chain.inverse_kinematics(xyz, [0, 0, -1], orientation_mode=self.down_axis, initial_position=q0)
            if np.linalg.norm(self.chain.forward_kinematics(q)[:3, 3] - xyz) > 0.01:
                q = self.chain.inverse_kinematics(xyz, initial_position=q0)  # unreachable straight down: position only
        else:
            q = self.chain.inverse_kinematics(xyz, initial_position=q0)
        err = float(np.linalg.norm(self.chain.forward_kinematics(q)[:3, 3] - xyz))
        if err > 0.015:
            raise RuntimeError(f"target {np.round(xyz, 3)} m is out of reach (IK error {err * 1000:.0f} mm)")
        return {j: math.degrees(q[i]) for j, i in self.index.items()}


# ---------- table <-> robot ----------

def fit_table_to_robot(table_mm: np.ndarray, robot_m: np.ndarray) -> dict:
    """2D rigid fit (rotation + translation, no scale) from table mm to robot metres, plus table height."""
    a = table_mm[:, :2] / 1000.0
    b = robot_m[:, :2]
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    u, _, vt = np.linalg.svd((a - ca).T @ (b - cb))
    r = (u @ vt).T
    if np.linalg.det(r) < 0:  # reflection: table axes don't match how the markers were measured
        raise RuntimeError("table axes look mirrored - check the marker positions in scene/config.json")
    t = cb - r @ ca
    resid = np.linalg.norm((a @ r.T + t) - b, axis=1)
    return {"R": r.tolist(), "t": t.tolist(), "table_z": float(robot_m[:, 2].mean()),
            "rms_mm": float(np.sqrt((resid**2).mean()) * 1000), "points": len(a)}


def table_point(calib: dict, x_mm: float, y_mm: float, height_m: float) -> np.ndarray:
    xy = np.asarray(calib["R"]) @ (np.array([x_mm, y_mm]) / 1000.0) + np.asarray(calib["t"])
    return np.array([xy[0], xy[1], calib["table_z"] + height_m])


def calibrate(port: str) -> None:
    from scene.markers import TableConfig

    points = [(f"marker {mid}", x, y) for mid, (x, y) in sorted(TableConfig.load().markers.items())]
    objects = json.loads(OBJECTS.read_text())
    points += [(f"object '{o['name']}'", o["x"], o["y"]) for o in objects.get("objects", [])]
    if "drop" in objects:
        points.append(("drop spot", objects["drop"]["x"], objects["drop"]["y"]))
    cal = load_robot_calibration()
    kin = Kinematics()
    bus = open_bus(port)
    table, robot = [], []
    try:
        bus.disable_torque()
        print("The arm is limp. For each point, rest the gripper TIP on its centre and press Enter.")
        print("Type s + Enter to skip a point the arm can't reach, q + Enter to finish early (need 3+ points).")
        for label, x, y in points:
            answer = input(f"  {label} at ({x:.0f}, {y:.0f}) mm: ").strip().lower()
            if answer.startswith("q"):
                break
            if answer.startswith("s"):
                continue
            tip = kin.fk(to_deg(read(bus), cal))[:3, 3]
            print(f"    robot frame: {np.round(tip * 1000).astype(int)} mm")
            table.append([x, y, 0.0])
            robot.append(tip)
    finally:
        bus.disconnect(disable_torque=True)
    if len(table) < 3:
        raise SystemExit(f"Only {len(table)} points touched - need at least 3. Run it again.")
    result = fit_table_to_robot(np.array(table), np.array(robot))
    TABLE_CALIB.write_text(json.dumps(result, indent=2))
    print(f"Saved {TABLE_CALIB.name}: fit error {result['rms_mm']:.1f} mm over {result['points']} points "
          f"(over ~10 mm: re-touch more carefully, or the marker / object positions are off)")


# ---------- pick ----------

def _setup(name: str):
    """Everything a move needs: the object, drop spot, table fit, robot calibration and kinematics."""
    objects = json.loads(OBJECTS.read_text())
    obj = next((o for o in objects["objects"] if o["name"] == name), None)
    if obj is None:
        raise RuntimeError(f"{name!r} is not in gaze/objects.json")
    if not TABLE_CALIB.exists():
        raise RuntimeError("no arm/table_to_robot.json - run: scripted.cmd calibrate")
    table = json.loads(TABLE_CALIB.read_text())
    return obj, objects.get("drop"), table, load_robot_calibration(), Kinematics()  # drop None = hand over


def _run(bus, kin, cal, waypoints, on_waypoint=None) -> dict[str, float]:
    """Solve every waypoint first (nothing moves if any is unreachable), then glide through them.

    waypoints: [(xyz metres, gripper %), ...]; on_waypoint(i) is called just before moving to waypoint i.
    """
    seed = to_deg(read(bus), cal)
    plan = []
    for xyz, grip in waypoints:
        seed = kin.ik(xyz, seed)
        plan.append((dict(seed), grip))
    for i, (deg, grip) in enumerate(plan):
        if on_waypoint:
            on_waypoint(i)
        target = {**to_raw(deg, cal), "gripper": gripper_raw(grip, cal)}
        start = read(bus)
        glide(bus, start, target, glide_time(start, target))
        time.sleep(0.25 if grip == GRIPPER_CLOSED else 0.1)
    return seed


def hover(name: str, port: str = "COM4") -> None:
    """Move above the object with the gripper open, and keep holding there (no descent)."""
    obj, _, table, cal, kin = _setup(name)
    bus = open_bus(port)
    try:
        hold(bus)
        _run(bus, kin, cal, [(table_point(table, obj["x"], obj["y"], HOVER_M), GRIPPER_OPEN)])
    finally:
        bus.disconnect(disable_torque=False)


def pick_from_hover(name: str, port: str = "COM4", on_descend=None) -> bool:
    """Descend, grasp, lift, then drop it at the drop spot - or hand it over if there is none.
    Returns True if the gripper held something.

    on_descend() is called right before the arm starts moving down (log the real descent there).
    """
    obj, drop_spot, table, cal, kin = _setup(name)
    above = table_point(table, obj["x"], obj["y"], HOVER_M)
    at = table_point(table, obj["x"], obj["y"], GRASP_M)
    if drop_spot:
        finish = [(above, GRIPPER_CLOSED), (table_point(table, drop_spot["x"], drop_spot["y"], HOVER_M), GRIPPER_CLOSED)]
    else:  # hand-over: straight up, hold it there for the user
        finish = [(table_point(table, obj["x"], obj["y"], HANDOVER_M), GRIPPER_CLOSED)]
    bus = open_bus(port)
    held = False
    try:
        hold(bus)
        _run(bus, kin, cal, [(above, GRIPPER_OPEN), (at, GRIPPER_OPEN), (at, GRIPPER_CLOSED)],
             on_waypoint=lambda i: on_descend() if i == 1 and on_descend else None)
        # An empty gripper closes all the way; one holding an object stops short of the target.
        grip_now = read(bus)["gripper"]
        held = grip_now > gripper_raw(GRIPPER_CLOSED, cal) + 0.08 * (cal["gripper"]["range_max"] - cal["gripper"]["range_min"])
        last = _run(bus, kin, cal, finish)
        if not drop_spot:
            time.sleep(HANDOVER_S)  # the user takes it
        release = {**to_raw(last, cal), "gripper": gripper_raw(GRIPPER_OPEN, cal)}
        start = read(bus)
        glide(bus, start, release, 0.6)
    finally:
        bus.disconnect(disable_torque=False)  # keep holding; the caller glides home
    return held


def pick(name: str, port: str = "COM4") -> bool:
    """Full scripted pick: hover, descend, grasp, then drop or hand over. Returns True if something was held."""
    hover(name, port)
    return pick_from_hover(name, port)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["calibrate", "pick"])
    parser.add_argument("name", nargs="?", help="Object name for pick")
    parser.add_argument("--port", default="COM4")
    args = parser.parse_args()
    if args.action == "calibrate":
        calibrate(args.port)
    else:
        if not args.name:
            raise SystemExit("usage: scripted.cmd pick <object name>")
        pick(args.name, args.port)


if __name__ == "__main__":
    main()
