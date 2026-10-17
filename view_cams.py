"""Show the XIAO eye-camera feeds side by side in one window.

Each OpenIris USB board shows up in Windows as a webcam named "openiristracker".
Only one program can use a camera at a time, so close EyeTrackVR first.

Usage (inside `conda activate lerobot`):
    python view_cams.py                 # cameras 1, 2 and 3
    python view_cams.py 2 3             # just these cameras
    python view_cams.py --scale 3       # bigger window

Keys: q or Esc quits, s saves a snapshot of every feed into ./snapshots
"""

import argparse
import os
import time
from pathlib import Path

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

SIZE = 240  # OpenIris frames are 240x240


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cameras", nargs="*", type=int, default=[1, 2, 3], help="Camera numbers")
    parser.add_argument("--scale", type=float, default=2, help="Zoom factor for the window")
    args = parser.parse_args()

    caps = {}
    for index in args.cameras:
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            caps[index] = cap
        else:
            print(f"Camera {index}: could not open (in use by another app, or not plugged in)")
    if not caps:
        raise SystemExit("No cameras opened. Close EyeTrackVR and try again.")

    blank = np.zeros((SIZE, SIZE, 3), np.uint8)
    frames = {i: blank for i in caps}
    fps = {i: 0.0 for i in caps}
    last = {i: time.time() for i in caps}
    snapshots = Path("snapshots")

    print("Showing cameras", ", ".join(map(str, caps)), "- press q to quit, s to save a snapshot")
    while True:
        for i, cap in caps.items():
            ok, frame = cap.read()
            if ok:
                now = time.time()
                fps[i] = 0.9 * fps[i] + 0.1 / max(now - last[i], 1e-3)
                last[i] = now
                frames[i] = cv2.resize(frame, (SIZE, SIZE))
        tiles = []
        for i in caps:
            tile = frames[i].copy()
            stale = time.time() - last[i] > 1
            label = f"cam {i}  {'NO SIGNAL' if stale else f'{fps[i]:.0f} fps'}"
            cv2.putText(tile, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255) if stale else (0, 255, 0), 1)
            tiles.append(tile)
        view = np.hstack(tiles)
        cv2.imshow("XIAO eye cameras", cv2.resize(view, None, fx=args.scale, fy=args.scale))

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27) or cv2.getWindowProperty("XIAO eye cameras", cv2.WND_PROP_VISIBLE) < 1:
            break
        if key == ord("s"):
            snapshots.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            for i in caps:
                cv2.imwrite(str(snapshots / f"cam{i}-{stamp}.png"), frames[i])
            print(f"Saved snapshots to {snapshots.resolve()}")

    for cap in caps.values():
        cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
