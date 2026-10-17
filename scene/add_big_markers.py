"""Add the big head-camera markers (ids 4-7) to scene/config.json by locating them with the side camera.

The side camera's table fit (from the small markers 0-3) turns each big marker's centre in its picture into
table mm, so nothing has to be measured by hand. Run with objects.cmd running and LOCKED (it reads the
snapshots objects.cmd saves), the big markers lying flat with their labelled edge away from you:
    python -m scene.add_big_markers
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scene.make_big_markers import IDS, SIZE_MM  # noqa: E402
from scene.markers import DICTIONARY, TableConfig, pixels_to_table  # noqa: E402

SNAPSHOT = ROOT / "logs" / "side_check.jpg"
HOLD = ROOT / "logs" / "side_table_hold.json"


def main() -> None:
    if not HOLD.exists():
        raise SystemExit("The side camera isn't locked yet (no logs/side_table_hold.json) - run objects.cmd first.")
    H = np.array(json.loads(HOLD.read_text())["H"])
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICTIONARY), params)
    found: dict[int, list] = {i: [] for i in IDS}
    last, end = None, time.time() + 6
    while time.time() < end:  # several snapshots: average, and catch markers the arm briefly hides
        img = cv2.imread(str(SNAPSHOT))
        if img is None or (last is not None and np.array_equal(img, last)):
            time.sleep(0.2)
            continue
        last = img
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = det.detectMarkers(gray)
        for c, mid in zip(corners, ids.flatten() if ids is not None else []):
            if int(mid) in found:
                found[int(mid)].append(pixels_to_table(H, c.reshape(4, 2)).mean(axis=0))
    config = TableConfig.load()
    for mid, pts in found.items():
        if not pts:
            print(f"  id {mid}: not seen by the side camera - check it's flat and in its view")
            continue
        x, y = np.median(np.array(pts), axis=0)
        config.markers[mid] = (round(float(x), 1), round(float(y), 1))
        config.sizes[mid] = SIZE_MM
        print(f"  id {mid}: at ({x:.0f}, {y:.0f}) mm  ({len(pts)} sightings)")
    config.save()
    print("saved scene/config.json - restart objects.cmd and gaze.cmd so they pick it up")


if __name__ == "__main__":
    main()
