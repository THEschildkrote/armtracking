"""The arm's two training cameras: settings shared by the check tool, record.cmd and eval.cmd.

arm/cameras.json:
    {"front": {"usb_id": "VID_32E6&PID_9005", "index": 2, "rotation": 0}, "wrist": {...}}

Cameras are found by usb_id (the two kit cameras are different models), so the
DirectShow number can change when things are re-plugged without swapping the
views. index is only the fallback when the usb_id is not found. rotation is
0, 90, 180 or -90 (LeRobot's convention), applied to every recorded frame.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("cameras.json")
REFERENCE_DIR = Path(__file__).with_name("camera_reference")
DSHOW = 700  # cv2.CAP_DSHOW: same numbering as view_cams.py / eye.cmd / front.cmd
DEFAULTS = {"front": {"usb_id": "", "index": 1, "rotation": 0}, "wrist": {"usb_id": "", "index": 2, "rotation": 0}}
ROTATIONS = (0, 90, 180, -90)
FFMPEG = Path(os.environ.get("USERPROFILE", "")) / "miniconda3" / "envs" / "lerobot" / "Library" / "bin" / "ffmpeg.exe"


def video_devices() -> list[tuple[str, str]]:
    """DirectShow video devices in camera-number order: [(name, 'VID_xxxx&PID_xxxx'), ...]."""
    try:
        out = subprocess.run(
            [str(FFMPEG), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=True, timeout=15,
        ).stderr
    except (OSError, subprocess.TimeoutExpired):
        return []
    devices, lines = [], out.splitlines()
    for i, line in enumerate(lines):
        m = re.search(r'"(.+)" \(video\)', line)
        if m:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            ids = re.search(r"vid_([0-9a-f]{4})&pid_([0-9a-f]{4})", nxt, re.I)
            devices.append((m.group(1), f"VID_{ids.group(1)}&PID_{ids.group(2)}".upper() if ids else ""))
    return devices


def load() -> dict[str, dict]:
    cams = {k: dict(v) for k, v in DEFAULTS.items()}
    if CONFIG_PATH.exists():
        for name, c in json.loads(CONFIG_PATH.read_text()).items():
            cams[name] = {**cams.get(name, {}), **c}
    devices = video_devices()
    for c in cams.values():
        if c.get("usb_id"):
            for idx, (_, usb_id) in enumerate(devices):
                if usb_id == c["usb_id"]:
                    c["index"] = idx
                    break
    return cams


def save(cams: dict[str, dict]) -> None:
    devices = video_devices()
    for c in cams.values():
        if 0 <= c["index"] < len(devices):
            c["usb_id"] = devices[c["index"]][1]
    CONFIG_PATH.write_text(json.dumps(cams, indent=2))


def open_order(cams: dict[str, dict]) -> list[str]:
    """Wrist first: the front camera only does uncompressed YUYV and, opened first, takes so
    much USB bandwidth on the shared hub that the wrist camera then fails to open."""
    return sorted(cams, key=lambda name: name != "wrist")


def lerobot_arg(fps: int, cams: dict[str, dict] | None = None) -> str:
    """The --robot.cameras=... value for lerobot-record / lerobot-rollout (connected in this order)."""
    cams = cams or load()
    parts = [
        f"{name}: {{type: opencv, index_or_path: {cams[name]['index']}, width: 640, height: 480, fps: {fps}, "
        f"fourcc: MJPG, backend: {DSHOW}, rotation: {cams[name]['rotation']}}}"
        for name in open_order(cams)
    ]
    return "{" + ", ".join(parts) + "}"
