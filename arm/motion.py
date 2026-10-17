"""Low-level follower moves shared by the home tool, the arm runner and the scripted pick.

Works on raw servo positions (0-4095) through LeRobot's motor bus, so it needs no
policy, camera or calibration prompt. Every move is an eased glide.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

from lerobot.motors import Motor, MotorNormMode, SerialMotorsBus

MOTORS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
HOME_PATH = Path(__file__).with_name("home_pose.json")
RATE_HZ = 50


def open_bus(port: str) -> SerialMotorsBus:
    bus = SerialMotorsBus(
        port=port,
        motors={name: Motor(i, "sts3215", MotorNormMode.RANGE_M100_100) for i, name in enumerate(MOTORS, 1)},
    )
    bus.connect(handshake=False)
    missing = [name for name in MOTORS if bus.ping(name, num_retry=2) is None]
    if missing:
        bus.disconnect(disable_torque=False)
        raise RuntimeError(f"Follower on {port}: no answer from {', '.join(missing)} (power? cable?)")
    return bus


def read(bus: SerialMotorsBus) -> dict[str, int]:
    return {k: int(v) for k, v in bus.sync_read("Present_Position", normalize=False).items()}


def hold(bus: SerialMotorsBus) -> dict[str, int]:
    """Torque on at the current pose (no jump), position mode."""
    bus.disable_torque()
    bus.set_operating_mode("position")
    here = read(bus)
    bus.sync_write("Goal_Position", here, normalize=False)
    bus.enable_torque()
    return here


def glide(bus: SerialMotorsBus, start: dict[str, int], target: dict[str, int], seconds: float) -> None:
    """Ease from start to target (only the joints in target)."""
    steps = max(1, int(seconds * RATE_HZ))
    for i in range(1, steps + 1):
        s = (1 - math.cos(math.pi * i / steps)) / 2
        goal = {k: int(round(start[k] + (target[k] - start[k]) * s)) for k in target}
        bus.sync_write("Goal_Position", goal, normalize=False)
        time.sleep(1 / RATE_HZ)


def glide_time(start: dict[str, int], target: dict[str, int], ticks_per_s: float = 900) -> float:
    """Duration for a calm move: the biggest joint change at ~80 deg/s, at least 0.6 s."""
    biggest = max(abs(target[k] - start[k]) for k in target)
    return max(0.6, biggest / ticks_per_s)


def load_home() -> dict[str, int]:
    if not HOME_PATH.exists():
        raise RuntimeError("No arm/home_pose.json - put the arm in its rest pose and run: home.cmd --save")
    return {k: int(v) for k, v in json.loads(HOME_PATH.read_text()).items()}


def go_home(port: str, relax: bool = True) -> None:
    """Glide the follower to the saved rest pose; relax=True turns torque off once it's there."""
    home = load_home()
    bus = open_bus(port)
    try:
        here = hold(bus)
        glide(bus, here, home, glide_time(here, home))
        time.sleep(0.3)
    finally:
        bus.disconnect(disable_torque=relax)
