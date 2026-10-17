"""Scripted pick: the fallback when a learned policy is not ready or fails at the venue.

Uses the SO-101 model (arm/so101/so101_new_calib.urdf, from TheRobotStudio/SO-ARM100)
with ikpy for inverse kinematics. Joint angles follow LeRobot's degree convention
(0 deg = middle of the calibrated range), which is what the "new_calib" model uses.

Two steps:
  1. table calibration (once per board placement): the arm goes limp, you touch the
     gripper tip to each table marker centre, skipping any it can't reach (at least 3); forward kinematics gives where the tip is in
     robot coordinates, and a 2D rigid fit maps table mm -> robot metres. Last, you touch
     the drop spot and its table position is saved to gaze/objects.json.
         scripted.cmd calibrate
     then, for each object (and "drop"), touch it once to record where it is:
         scripted.cmd teach granola
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

import cv2
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
HANDOVER_M = 0.12  # hand-over height above the table (no drop spot); ~0.13 is the most it reaches pointing down
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
        # same arm with the wrist roll held where it is (inactive joints keep their initial value)
        self.chain_fixed_roll = Chain.from_urdf_file(
            str(URDF), base_elements=["base_link"],
            active_links_mask=[m and link.name != "wrist_roll" for m, link in zip(mask, chain.links)])
        self.names = [link.name for link in self.chain.links]
        self.index = {j: self.names.index(j) for j in ARM_JOINTS}
        self.down_axis = self._approach_axis()

    def _vector(self, deg: dict[str, float]) -> np.ndarray:
        q = np.zeros(len(self.names))
        for j, i in self.index.items():
            lo, hi = self.chain.links[i].bounds
            q[i] = min(max(math.radians(deg.get(j, 0.0)), lo if lo is not None else -np.inf),
                       hi if hi is not None else np.inf)  # the solver refuses a start outside the joint limits
        return q

    def _roll_ok(self, roll_deg: float) -> bool:
        lo, hi = self.chain.links[self.index["wrist_roll"]].bounds
        r = math.radians(roll_deg)
        return (lo is None or r >= lo) and (hi is None or r <= hi)

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

    def jaw_yaw(self, deg: dict[str, float]) -> float:
        """Heading (degrees, robot frame) of a fixed horizontal axis of the gripper while it points down.

        Picks compare it with the heading recorded by teach, so which axis it is doesn't matter.
        """
        k = next(i for i, axis in enumerate("XYZ") if axis != self.down_axis)
        v = self.fk(deg)[:3, k]
        return math.degrees(math.atan2(v[1], v[0]))

    def with_yaw(self, deg: dict[str, float], yaw: float, xyz) -> dict[str, float]:
        """Tip still at xyz pointing down, wrist rolled so jaw_yaw == yaw. Jaws are symmetric, so a
        half-turn either way grips the same: try the roll nearest its centre first, then the others.
        The tip sits off the roll axis, so after each roll the position is re-solved with the roll held."""
        best, best_err = None, float("inf")
        for choice in range(3):
            out = self._roll_to(deg, yaw, xyz, choice)
            err = float(np.linalg.norm(self.fk(out)[:3, 3] - xyz))
            if err <= 0.015:
                return out
            if err < best_err:
                best, best_err = out, err
        raise RuntimeError(f"can't reach {np.round(xyz, 3)} m with that grip angle (IK error {best_err * 1000:.0f} mm)")

    def _roll_to(self, deg: dict[str, float], yaw: float, xyz, choice: int) -> dict[str, float]:
        for step in range(4):
            probe = {**deg, "wrist_roll": deg["wrist_roll"] + 10}
            sign = 1.0 if _wrap(self.jaw_yaw(probe) - self.jaw_yaw(deg)) > 0 else -1.0
            delta = _wrap(yaw - self.jaw_yaw(deg))
            options = sorted((r for r in (deg["wrist_roll"] + sign * (delta + k * 180) for k in (-2, -1, 0, 1, 2))
                              if self._roll_ok(r)), key=abs) or [deg["wrist_roll"]]
            # first step: the chosen half-turn; after that stay with it (nearest to where the roll already is)
            roll = options[min(choice, len(options) - 1)] if step == 0 else min(options, key=lambda r: abs(r - deg["wrist_roll"]))
            deg = {**deg, "wrist_roll": roll}
            q = self.chain_fixed_roll.inverse_kinematics(xyz, [0, 0, -1], orientation_mode=self.down_axis,
                                                         initial_position=self._vector(deg))
            deg = {j: math.degrees(q[i]) for j, i in self.index.items()}
        return deg


def _wrap(angle: float) -> float:
    return (angle + 180) % 360 - 180


# ---------- table <-> robot ----------

def fit_table_to_robot(table_mm: np.ndarray, robot_m: np.ndarray) -> dict:
    """Map table mm -> robot metres from touched points.

    rigid (rotation + translation) is the honest check: its error shows how far the arm's
    model and the measured marker positions disagree. With 4+ points an affine map (adds
    scale and shear) plus a tilted table plane is fitted; its error is the reported fit
    error. Moves use a homography through the same points: it also follows the trapezoid
    a reach-dependent arm-model error makes, and lands exactly on the touched markers.
    """
    a = table_mm[:, :2] / 1000.0
    b = robot_m[:, :2]
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    u, _, vt = np.linalg.svd((a - ca).T @ (b - cb))
    r = (u @ vt).T
    if np.linalg.det(r) < 0:  # reflection: table axes don't match how the markers were measured
        raise RuntimeError("table axes look mirrored - check the marker positions in scene/config.json")
    t = cb - r @ ca
    rigid_err = np.linalg.norm((a @ r.T + t) - b, axis=1)
    result = {"R": r.tolist(), "t": t.tolist(), "table_z": float(robot_m[:, 2].mean()),
              "rigid_mm": float(np.sqrt((rigid_err**2).mean()) * 1000), "points": len(a),
              "touched": {"table_mm": table_mm[:, :2].tolist(), "robot_m": robot_m.tolist()}}
    err = rigid_err
    if len(a) >= 4:
        h = np.c_[a, np.ones(len(a))]
        result["A"] = np.linalg.lstsq(h, b, rcond=None)[0].T.tolist()  # 2x3: xy = A @ [x, y, 1]
        result["z_plane"] = np.linalg.lstsq(h, robot_m[:, 2], rcond=None)[0].tolist()  # z = p . [x, y, 1]
        err = np.linalg.norm(h @ np.asarray(result["A"]).T - b, axis=1)
        H, _ = cv2.findHomography(a.astype(np.float64), b.astype(np.float64), 0)
        if H is not None:
            result["H"] = H.tolist()  # table metres -> robot x, y metres
    result["per_point_mm"] = (err * 1000).round(1).tolist()
    result["rms_mm"] = float(np.sqrt((err**2).mean()) * 1000)
    return result


def table_point(calib: dict, x_mm: float, y_mm: float, height_m: float) -> np.ndarray:
    p = np.array([x_mm / 1000.0, y_mm / 1000.0, 1.0])
    if "H" in calib:
        q = np.asarray(calib["H"]) @ p
        xy = q[:2] / q[2]
        z = float(np.asarray(calib["z_plane"]) @ p)
    elif "A" in calib:
        xy = np.asarray(calib["A"]) @ p
        z = float(np.asarray(calib["z_plane"]) @ p)
    else:
        xy = np.asarray(calib["R"]) @ p[:2] + np.asarray(calib["t"])
        z = calib["table_z"]
    return np.array([xy[0], xy[1], z + height_m])


def table_from_robot(calib: dict, xy_m: np.ndarray) -> np.ndarray:
    """Robot-frame x, y (metres) -> table mm (inverse of table_point)."""
    if "H" in calib:
        q = np.linalg.inv(np.asarray(calib["H"])) @ np.r_[np.asarray(xy_m, float), 1.0]
        return q[:2] / q[2] * 1000
    if "A" in calib:
        m = np.asarray(calib["A"])
        return np.linalg.solve(m[:, :2], np.asarray(xy_m) - m[:, 2]) * 1000
    return np.asarray(calib["R"]).T @ (np.asarray(xy_m) - np.asarray(calib["t"])) * 1000


def table_heading(calib: dict, x_mm: float, y_mm: float) -> float:
    """Heading (degrees, robot frame) of the table's +x axis at a table point."""
    d = table_point(calib, x_mm + 10, y_mm, 0) - table_point(calib, x_mm, y_mm, 0)
    return math.degrees(math.atan2(d[1], d[0]))


def report(result: dict) -> None:
    """Explain a fit: per-point error and, side by side, marker spacing measured vs as the arm felt it."""
    tm = np.asarray(result["touched"]["table_mm"])
    rm = np.asarray(result["touched"]["robot_m"]) * 1000
    print("  point error (mm):", result["per_point_mm"])
    print("  tip height per point (mm):", rm[:, 2].round(0).tolist(), "(should all be about equal)")
    for i in range(len(tm)):
        for j in range(i + 1, len(tm)):
            print(f"  {tm[i].round(0).tolist()} -> {tm[j].round(0).tolist()}: measured {np.linalg.norm(tm[i] - tm[j]):5.0f} mm,"
                  f" arm says {np.linalg.norm(rm[i, :2] - rm[j, :2]):5.0f} mm")
    print(f"  rigid fit error {result['rigid_mm']:.1f} mm (measured layout vs arm model)")


def calibrate(port: str) -> None:
    from scene.markers import TableConfig

    points = [(f"marker {mid}", x, y) for mid, (x, y) in sorted(TableConfig.load().markers.items())]
    objects = json.loads(OBJECTS.read_text())
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
        # the drop spot isn't measured: touch it and its table position is worked out from the fit
        have = " (Enter on its own keeps the current one)" if "drop" in objects else ""
        answer = input("  DROP spot: rest the tip where items should be released, then type d + Enter"
                       f"{have}; n + Enter = no drop spot, hand items over instead: ").strip().lower()
        drop_tip = kin.fk(to_deg(read(bus), cal))[:3, 3] if answer.startswith("d") else None
    finally:
        bus.disconnect(disable_torque=True)
    if len(table) < 3:
        raise SystemExit(f"Only {len(table)} points touched - need at least 3. Run it again.")
    result = fit_table_to_robot(np.array(table), np.array(robot))
    TABLE_CALIB.write_text(json.dumps(result, indent=2))
    if drop_tip is not None or answer.startswith("n"):
        if drop_tip is not None:
            x, y = table_from_robot(result, drop_tip[:2])
            objects["drop"] = {"x": round(float(x)), "y": round(float(y))}
            print(f"Drop spot saved at table ({x:.0f}, {y:.0f}) mm")
        else:
            objects.pop("drop", None)
            print("No drop spot: picks hand the item over")
        OBJECTS.write_text(json.dumps(objects, indent=2))
    report(result)
    print(f"Saved {TABLE_CALIB.name}: fit error {result['rms_mm']:.1f} mm over {result['points']} points "
          f"(over ~10 mm: re-touch more carefully, or the marker positions are off)")


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

    waypoints: [(xyz metres, gripper %[, jaw heading degrees]), ...]; on_waypoint(i) is called just
    before moving to waypoint i.
    """
    seed = to_deg(read(bus), cal)
    plan = []
    for xyz, grip, *yaw in waypoints:
        seed = kin.ik(xyz, seed)
        if yaw and yaw[0] is not None:
            seed = kin.with_yaw(seed, yaw[0], xyz)
        plan.append((dict(seed), grip))
    for i, (deg, grip) in enumerate(plan):
        if on_waypoint:
            on_waypoint(i)
        target = {**to_raw(deg, cal), "gripper": gripper_raw(grip, cal)}
        start = read(bus)
        glide(bus, start, target, glide_time(start, target))
        time.sleep(0.25 if grip == GRIPPER_CLOSED else 0.1)
    return seed


def grip_yaw(table: dict, obj: dict) -> float | None:
    """The jaw heading recorded for an object by teach (robot frame), or None to leave the wrist as IK has it."""
    if obj.get("yaw") is None:
        return None
    return obj["yaw"] + table_heading(table, obj["x"], obj["y"])


def heights(obj: dict) -> tuple[float, float, float]:
    """(grasp, hover, hand-over) tip heights above the table in metres for this object.

    grasp_mm is recorded by teach (where the jaws close - near the table for a flat bar,
    up at the cap for a bottle); hover and hand-over keep the same clearance above it.
    """
    grasp = obj.get("grasp_mm", GRASP_M * 1000) / 1000
    hover = grasp + HOVER_M - GRASP_M
    return grasp, hover, max(HANDOVER_M, hover)


def reachable_height(kin, table: dict, x: float, y: float, yaw: float | None, want: float, lowest: float) -> float:
    """The highest tip height from want down to lowest (metres above the table) the arm reaches pointing
    down at that grip angle. Far out on the board it can't get as high, so the approach comes in lower."""
    seed = {k: 0.0 for k in ARM_JOINTS}
    h = want
    while h >= lowest - 1e-9:
        p = table_point(table, x, y, h)
        try:
            q = kin.ik(p, seed)
            if yaw is not None:
                kin.with_yaw(q, yaw, p)
            return h
        except RuntimeError:
            h -= 0.01
    return want  # nothing reachable: let the move report it (nothing moves)


def plan(kin, table: dict, obj: dict) -> dict:
    """Where to go for this object at its current spot.

    above: straight-down approach right over it at the grip angle - as high as the arm reaches,
           which far out on the board can be only a little over the grip.
    high:  the full hover height, gripper tilted if that's the only way to get there; used before
           and after the low approach when "above" had to come down (None when it didn't).
    """
    grasp, hover_h, handover_h = heights(obj)
    yaw = grip_yaw(table, obj)
    low = reachable_height(kin, table, obj["x"], obj["y"], yaw, hover_h, grasp + 0.008)
    lowered = low < hover_h - 1e-9
    return {"yaw": yaw, "at": table_point(table, obj["x"], obj["y"], grasp),
            "above": table_point(table, obj["x"], obj["y"], low),
            "high": table_point(table, obj["x"], obj["y"], hover_h) if lowered else None,
            "lift": table_point(table, obj["x"], obj["y"], max(handover_h, hover_h)),
            "hover_h": hover_h}


def hover(name: str, port: str = "COM4") -> None:
    """Move above the object with the gripper open, and keep holding there (no descent)."""
    obj, _, table, cal, kin = _setup(name)
    route = plan(kin, table, obj)
    target = (route["high"], GRIPPER_OPEN, None) if route["high"] is not None else (route["above"], GRIPPER_OPEN, route["yaw"])
    bus = open_bus(port)
    try:
        hold(bus)
        _run(bus, kin, cal, [target])
    finally:
        bus.disconnect(disable_torque=False)


def pick_from_hover(name: str, port: str = "COM4", on_descend=None) -> bool:
    """Descend, grasp, lift, then drop it at the drop spot - or hand it over if there is none.
    Returns True if the gripper held something.

    on_descend() is called right before the arm starts moving down (log the real descent there).
    """
    obj, drop_spot, table, cal, kin = _setup(name)
    route = plan(kin, table, obj)
    yaw, above, at, high = route["yaw"], route["above"], route["at"], route["high"]
    up = [(above, GRIPPER_CLOSED, yaw)] + ([(high, GRIPPER_CLOSED)] if high is not None else [])
    if drop_spot:
        finish = up + [(table_point(table, drop_spot["x"], drop_spot["y"], route["hover_h"]), GRIPPER_CLOSED)]
    else:  # hand-over: up, hold it there for the user
        finish = up + [(route["lift"], GRIPPER_CLOSED)]
    down = ([(high, GRIPPER_OPEN)] if high is not None else []) + [(above, GRIPPER_OPEN, yaw), (at, GRIPPER_OPEN, yaw),
                                                                    (at, GRIPPER_CLOSED, yaw)]
    first_down = 1  # waypoint 0 is where hover() left the arm; the move to waypoint 1 goes down
    bus = open_bus(port)
    held = False
    try:
        hold(bus)
        _run(bus, kin, cal, down, on_waypoint=lambda i: on_descend() if i == first_down and on_descend else None)
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


def teach(name: str, port: str = "COM4") -> None:
    """Record where an object (or the drop spot, name "drop") sits by touching it with the limp arm."""
    if not TABLE_CALIB.exists():
        raise SystemExit("no arm/table_to_robot.json - run: scripted.cmd calibrate")
    table = json.loads(TABLE_CALIB.read_text())
    cal, kin = load_robot_calibration(), Kinematics()
    bus = open_bus(port)
    try:
        bus.disable_torque()
        if name == "drop":
            input("The arm is limp. Rest the gripper tip on the drop spot, then press Enter: ")
        else:
            input(f"The arm is limp. Put the open jaws around {name!r} exactly where they should grip it: pointing\n"
                  "straight down, turned to close across it, at the height to grip (low for a flat bar, at the\n"
                  "middle for a bottle lying on its side). Then press Enter: ")
        deg = to_deg(read(bus), cal)
        tip = kin.fk(deg)[:3, 3]
    finally:
        bus.disconnect(disable_torque=True)
    x, y = (round(float(v)) for v in table_from_robot(table, tip[:2]))
    objects = json.loads(OBJECTS.read_text())
    if name == "drop":
        objects["drop"] = {"x": x, "y": y}
    else:
        obj = next((o for o in objects["objects"] if o["name"] == name), None)
        if obj is None:
            obj = {"name": name, "cls": name}
            objects["objects"].append(obj)
        grasp_mm = max(5, round((tip[2] - table_point(table, x, y, 0)[2]) * 1000))  # never aim into the table
        obj.update(x=x, y=y, yaw=round(_wrap(kin.jaw_yaw(deg) - table_heading(table, x, y)), 1), grasp_mm=grasp_mm)
        # the class remembers how to grip it, for live detection to apply wherever it turns up
        from scene.detect import load_classes, save_classes

        classes = load_classes()
        cls = next((c for c, info in classes.items() if name in (c, info.get("name"))), obj.get("cls", name))
        info = classes.setdefault(cls, {"name": name, "prompt": cls.replace("_", " ")})
        info["grasp_mm"] = grasp_mm
        info["taught"] = {"x": x, "y": y, "yaw": obj["yaw"], "t": time.time()}
        save_classes(classes)
    OBJECTS.write_text(json.dumps(objects, indent=2))
    if name != "drop":
        try:  # the approach starts above the grip: check the arm can get there pointing down
            p = table_point(table, x, y, heights(obj)[0] + 0.008)  # the lowest straight-down approach
            kin.with_yaw(kin.ik(p, deg), grip_yaw(table, obj), p)
        except RuntimeError:
            print("WARNING: the arm can't come straight down onto this grip - grip lower"
                  " (lay tall things on their side) or move it closer to the arm")
    print(f"{name} saved at table ({x}, {y}) mm" +
          ("" if name == "drop" else f", grip angle {obj['yaw']} deg, grip height {obj['grasp_mm']} mm"))


def pick(name: str, port: str = "COM4") -> bool:
    """Full scripted pick: hover, descend, grasp, then drop or hand over. Returns True if something was held."""
    hover(name, port)
    return pick_from_hover(name, port)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["calibrate", "teach", "pick"])
    parser.add_argument("name", nargs="?", help="Object name for teach / pick (teach drop = the drop spot)")
    parser.add_argument("--port", default="COM4")
    args = parser.parse_args()
    if args.action == "calibrate":
        calibrate(args.port)
    elif args.action == "teach":
        if not args.name:
            raise SystemExit("usage: scripted.cmd teach <object name | drop>")
        teach(args.name, args.port)
    else:
        if not args.name:
            raise SystemExit("usage: scripted.cmd pick <object name>")
        pick(args.name, args.port)


if __name__ == "__main__":
    main()
