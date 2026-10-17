"""Live side-camera check for aiming and focusing: shows which table markers are read and how sharp it is.

    side_check.cmd        (q or Esc to quit; close it before running anything else that uses the side camera)

Aim for: all four markers boxed in green, "LOCKED" with a fit under 10 mm, and turn the lens
until the sharpness number peaks.
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from arm import cameras as armcams  # noqa: E402
from scene.markers import TableConfig, TableTracker  # noqa: E402

SNAPSHOT = Path(__file__).resolve().parents[1] / "logs" / "side_check.jpg"  # raw frame, refreshed every second


def main() -> None:
    devices = armcams.video_devices() or armcams.video_devices()
    cfg = armcams.load()["front"]  # the arm's "front" camera is the side camera
    idx = {usb: i for i, (_, usb) in enumerate(devices)}.get(cfg.get("usb_id"))
    if idx is None:
        raise SystemExit(f"side camera ({cfg.get('usb_id')}) not plugged in")
    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(cfg.get("rotation", 0))
    config = TableConfig.load()
    tracker = TableTracker(config, static=True)
    best, saved = 0.0, 0.0
    SNAPSHOT.parent.mkdir(exist_ok=True)
    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.05)
            continue
        if rot is not None:
            frame = cv2.rotate(frame, rot)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        best = max(best, sharp)
        s = tracker.process(frame, time.time())
        view = frame.copy()
        for mid, corners in s.marker_px.items():
            pts = corners.astype(np.int32)
            cv2.polylines(view, [pts], True, (0, 255, 0), 2)
            cv2.putText(view, str(mid), tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        missing = sorted(set(config.markers) - set(s.marker_px))
        held = s.valid and len(s.marker_px) < len(config.markers)
        status = (f"LOCKED{' (held)' if held else ''}  fit {s.reproj_mm:.1f} mm" if s.valid
                  else f"not locked (fit {s.reproj_mm:.0f} mm)" if s.H is not None else "not locked")
        lines = [status, f"markers seen {sorted(s.marker_px)}  missing {missing}",
                 f"sharpness {sharp:.0f}  (best so far {best:.0f})"]
        for i, text in enumerate(lines):
            colour = (0, 200, 0) if (i == 0 and s.valid) else (0, 0, 255) if i == 0 else (255, 255, 255)
            cv2.putText(view, text, (10, 28 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
            cv2.putText(view, text, (10, 28 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
        if time.time() - saved > 1.0:  # a copy others can look at while this holds the camera
            cv2.imwrite(str(SNAPSHOT), frame)
            cv2.imwrite(str(SNAPSHOT.with_name("side_check_view.jpg")), view)
            SNAPSHOT.with_suffix(".txt").write_text("\n".join(lines))
            saved = time.time()
        cv2.imshow("side camera check", view)
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
