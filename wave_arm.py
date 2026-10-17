"""Make an SO-101 arm wave hello.

What it does:
  1. Checks all 6 servos respond.
  2. Slowly lifts the arm from wherever it is to the "middle" pose you set during
     calibration (shoulder, elbow and wrist at the centre of their range).
  3. Waves the wrist up and down while swaying the base a little.
  4. Slowly returns to where it started, then turns torque off.

Every target is kept inside the joint ranges saved by `lerobot-calibrate`, so run
calibration first. Works for a follower or a leader arm (leader servos are weaker,
so it moves a bit softer).

Clear the space around the arm and keep a hand near the power plug the first time.
Ctrl+C during the wave makes it return to its start pose before stopping.

Usage (inside `conda activate lerobot`):
    python wave_arm.py COM4 --dry-run          # print the plan, no movement
    python wave_arm.py COM4                    # wave (uses calibration "my_follower")
    python wave_arm.py COM4 --id my_leader --waves 5 --size 300
"""

import argparse
import json
import math
import time
from pathlib import Path

from lerobot.motors import Motor, MotorNormMode, SerialMotorsBus

MOTORS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
CALIBRATION_DIR = Path.home() / ".cache" / "huggingface" / "lerobot" / "calibration"
MIDDLE = 2048  # raw position of the pose held at the first calibration prompt
READY_JOINTS = ["shoulder_lift", "elbow_flex", "wrist_flex"]
MARGIN = 100  # stay this far (raw ticks, ~9 deg) inside each calibrated limit
RATE_HZ = 50


def load_limits(calibration_id: str) -> tuple[dict[str, tuple[int, int]], Path]:
    files = sorted(CALIBRATION_DIR.rglob(f"{calibration_id}.json"))
    if not files:
        raise SystemExit(
            f"No calibration named '{calibration_id}' in {CALIBRATION_DIR}.\n"
            "Run lerobot-calibrate first, or pass --id with the name you calibrated with."
        )
    data = json.loads(files[0].read_text())
    limits = {name: (data[name]["range_min"] + MARGIN, data[name]["range_max"] - MARGIN) for name in MOTORS}
    return limits, files[0]


def clamp(goal: dict[str, float], limits: dict[str, tuple[int, int]]) -> dict[str, int]:
    return {name: int(round(min(max(pos, limits[name][0]), limits[name][1]))) for name, pos in goal.items()}


def move_to(bus, start: dict[str, int], target: dict[str, int], seconds: float) -> None:
    """Glide from start to target with an ease-in/ease-out profile.

    Both ends are poses the arm can reach, so every point in between is too.
    """
    steps = max(1, int(seconds * RATE_HZ))
    for i in range(1, steps + 1):
        s = (1 - math.cos(math.pi * i / steps)) / 2
        goal = {name: int(round(start[name] + (target[name] - start[name]) * s)) for name in target}
        bus.sync_write("Goal_Position", goal, normalize=False)
        time.sleep(1 / RATE_HZ)


def wave(bus, ready: dict[str, int], waves: int, size: int, limits) -> None:
    """Flap the wrist `waves` times while gently swaying the base."""
    period = 1.0
    total = waves * period
    steps = int(total * RATE_HZ)
    for i in range(steps + 1):
        t = i / RATE_HZ
        envelope = min(1.0, t / 0.5, (total - t) / 0.5)  # fade in and out so it never jerks
        goal = dict(ready)
        goal["wrist_flex"] = ready["wrist_flex"] + size * envelope * math.sin(2 * math.pi * t / period)
        goal["shoulder_pan"] = ready["shoulder_pan"] + size * 0.4 * envelope * math.sin(math.pi * t / period)
        bus.sync_write("Goal_Position", clamp(goal, limits), normalize=False)
        time.sleep(1 / RATE_HZ)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", help="Serial port, e.g. COM4")
    parser.add_argument("--id", default="my_follower", help="Calibration name used with lerobot-calibrate")
    parser.add_argument("--waves", type=int, default=3, help="Number of waves")
    parser.add_argument("--size", type=int, default=350, help="Wave size in raw ticks (350 is about 30 deg)")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without moving")
    args = parser.parse_args()

    limits, calibration_file = load_limits(args.id)
    print(f"Using calibration {calibration_file}")

    bus = SerialMotorsBus(
        port=args.port,
        motors={name: Motor(i, "sts3215", MotorNormMode.RANGE_M100_100) for i, name in enumerate(MOTORS, 1)},
    )
    bus.connect(handshake=False)
    missing = [name for name in MOTORS if bus.ping(name, num_retry=2) is None]
    if missing:
        bus.disconnect(disable_torque=False)
        raise SystemExit(f"Not responding: {', '.join(missing)}. Check power and cables.")

    home = bus.sync_read("Present_Position", normalize=False)
    ready = clamp({**home, **{name: MIDDLE for name in READY_JOINTS}}, limits)

    print("joint          start  ready  wave range")
    for name in MOTORS:
        swing = {"wrist_flex": args.size, "shoulder_pan": int(args.size * 0.4)}.get(name, 0)
        lo, hi = clamp({name: ready[name] - swing}, limits)[name], clamp({name: ready[name] + swing}, limits)[name]
        print(f"{name:<14}{home[name]:>6}{ready[name]:>7}  {lo}-{hi}")

    if args.dry_run:
        print("\nDry run: nothing moved.")
        bus.disconnect(disable_torque=False)
        return

    # Hold the current pose before turning torque on, so nothing jumps.
    bus.disable_torque()
    bus.set_operating_mode("position")
    bus.sync_write("Goal_Position", home, normalize=False)
    bus.enable_torque()

    try:
        print("\nRaising arm...")
        move_to(bus, home, ready, 3.0)
        print(f"Waving x{args.waves}!")
        wave(bus, ready, args.waves, args.size, limits)
    except KeyboardInterrupt:
        print("\nStopping - returning to start pose.")
    finally:
        current = bus.sync_read("Present_Position", normalize=False)
        print("Lowering arm...")
        move_to(bus, current, home, 3.0)
        time.sleep(0.5)
        bus.disconnect(disable_torque=True)
        print("Done - torque off.")


if __name__ == "__main__":
    main()
