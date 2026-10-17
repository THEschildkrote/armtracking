"""Live front-camera view: table markers, the table grid, and table mm under the mouse.

Works with the Wi-Fi front camera (Board B) or any USB camera. Table layout
(marker ids, centres in mm, marker size) lives in scene/config.json.

Usage (from the armproject folder):
    front.cmd --camera http://172.20.10.2/    # Board B over the hotspot
    front.cmd --camera 1                      # a USB camera

Keys:
    g      toggle the 50 mm table grid
    s      save a snapshot to snapshots/
    q/Esc  quit
Move the mouse over the picture to read the table position under it.
"""

import argparse
import os
import time
from pathlib import Path

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "fflags;nobuffer|flags;low_delay")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from gaze.capture import FRONT_MIRRORED  # noqa: E402
from scene.markers import CONFIG_PATH, TableConfig, TableTracker, pixels_to_table, table_to_pixels  # noqa: E402

WINDOW = "front"
GRID_MM = 50


def open_camera(source: str) -> cv2.VideoCapture:
    if source.isdigit():
        return cv2.VideoCapture(int(source), cv2.CAP_DSHOW)
    return cv2.VideoCapture(source)


def draw_grid(view, H, config: TableConfig, scale: float) -> None:
    xs = [p[0] for p in config.markers.values()]
    ys = [p[1] for p in config.markers.values()]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    for x in np.arange(x0, x1 + 1, GRID_MM):
        a, b = table_to_pixels(H, [[x, y0], [x, y1]]) * scale
        cv2.line(view, tuple(a.astype(int)), tuple(b.astype(int)), (255, 200, 0), 1)
    for y in np.arange(y0, y1 + 1, GRID_MM):
        a, b = table_to_pixels(H, [[x0, y], [x1, y]]) * scale
        cv2.line(view, tuple(a.astype(int)), tuple(b.astype(int)), (255, 200, 0), 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", default="http://172.20.10.2/", help="Camera number or stream URL")
    parser.add_argument("--scale", type=float, default=0, help="Window zoom (default: fit about 720 px)")
    args = parser.parse_args()

    config = TableConfig.load()
    if not CONFIG_PATH.exists():
        config.save()
        print(f"Wrote default table layout to {CONFIG_PATH} - measure your marker centres and edit it.")
    tracker = TableTracker(config)
    cap = open_camera(args.camera)
    if not cap.isOpened():
        raise SystemExit(f"Could not open {args.camera}. Is the hotspot on / the camera free?")

    mouse = [None]
    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, lambda e, x, y, f, p: mouse.__setitem__(0, (x, y)))
    show_grid, fps, last_t = True, 0.0, None
    print(__doc__.split("Keys:")[1])

    while True:
        ok, frame = cap.read()
        if not ok:
            if cv2.waitKey(100) & 0xFF in (ord("q"), 27):
                break
            continue
        if FRONT_MIRRORED and not args.camera.isdigit():
            frame = cv2.flip(frame, 1)  # Board B sends a mirrored picture
        t = time.monotonic()
        if last_t is not None:
            fps = 0.9 * fps + 0.1 / max(t - last_t, 1e-3)
        last_t = t

        sample = tracker.process(frame, t)

        scale = args.scale or max(1.0, 720 / max(frame.shape[:2]))
        view = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        view = cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        for marker_id, px in sample.marker_px.items():
            pts = (px * scale).astype(int)
            cv2.polylines(view, [pts], True, (0, 255, 0), 2)
            cv2.putText(view, str(marker_id), tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        if sample.H is not None and show_grid:
            draw_grid(view, sample.H, config, scale)

        state = "TABLE LOCKED" if sample.valid else "NO TABLE"
        colour = (0, 255, 0) if sample.valid else (0, 0, 255)
        lines = [
            f"{state}  {fps:4.1f} fps  {frame.shape[1]}x{frame.shape[0]}",
            f"markers {sorted(sample.marker_px)}  fit err {sample.reproj_mm:.1f} mm",
        ]
        if sample.H is not None and mouse[0] is not None:
            mx, my = mouse[0]
            tx, ty = pixels_to_table(sample.H, [[mx / scale, my / scale]])[0]
            lines.append(f"mouse -> table ({tx:.0f}, {ty:.0f}) mm")
            cv2.drawMarker(view, (mx, my), (0, 200, 255), cv2.MARKER_CROSS, 14, 1)
        for i, line in enumerate(lines):
            cv2.putText(view, line, (8, 22 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour if i == 0 else (255, 255, 255), 1)
        cv2.imshow(WINDOW, view)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            break
        if key == ord("g"):
            show_grid = not show_grid
        elif key == ord("s"):
            Path("snapshots").mkdir(exist_ok=True)
            path = Path("snapshots") / f"front-{time.strftime('%Y%m%d-%H%M%S')}.png"
            cv2.imwrite(str(path), frame)
            print(f"saved {path}")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
