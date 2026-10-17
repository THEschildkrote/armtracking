"""Live pupil view + the step-1 spread test.

Shows the eye camera with the fitted pupil ellipse, the frame rate and
(x, y, openness, valid). Settings are saved to eye/config.json.

Usage (inside `conda activate lerobot`, from the armproject folder):
    python -m eye.live                     # camera 1
    python -m eye.live --camera 2
    python -m eye.live --camera http://172.20.10.2/   # a Wi-Fi OpenIris board
    python -m eye.live --csv run1.csv      # also log every sample

Keys:
    r      drag a box around the eye (ROI), then Enter/Space; c cancels
    [ ]    minimum pupil contrast down / up (lower finds fainter pupils, higher rejects shadows)
    m      toggle the threshold mask view
    s      spread test: hold your gaze on one point for 2 s (blink frames are excluded)
    w      save settings to eye/config.json
    q/Esc  quit
"""

import argparse
import csv
import json
import os
import time
from dataclasses import asdict, fields
from pathlib import Path

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from eye.detector import DetectorConfig, PupilDetector, PupilSample  # noqa: E402
from eye.validity import EyeValidity  # noqa: E402

EVENT_COLOURS = {"long": (0, 200, 255), "double": (255, 120, 255), "blink": (180, 180, 180)}

CONFIG_PATH = Path(__file__).with_name("config.json")
SPREAD_SECONDS = 2.0
WINDOW = "eye"


def load_config() -> DetectorConfig:
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text())
        if data.get("roi"):
            data["roi"] = tuple(data["roi"])
        return DetectorConfig(**data)
    return DetectorConfig()


def open_camera(source: str) -> cv2.VideoCapture:
    if source.isdigit():
        return cv2.VideoCapture(int(source), cv2.CAP_DSHOW)
    return cv2.VideoCapture(source)


def spread_report(points: list[tuple[float, float]], total: int) -> str:
    if len(points) < 5:
        return f"spread: only {len(points)}/{total} valid samples - pupil lost?"
    arr = np.array(points)
    med = np.median(arr, axis=0)
    dist = np.linalg.norm(arr - med, axis=1)
    return (
        f"spread p95 {np.percentile(dist, 95):.1f}px  max {dist.max():.1f}px  "
        f"at ({med[0]:.0f},{med[1]:.0f})  valid {len(points)}/{total}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", default="1", help="Camera number, or a stream URL")
    parser.add_argument("--scale", type=int, default=3, help="Window zoom")
    parser.add_argument("--csv", help="Log every sample to this CSV file")
    args = parser.parse_args()

    config = load_config()
    detector = PupilDetector(config)
    cap = open_camera(args.camera)
    if not cap.isOpened():
        raise SystemExit(f"Could not open camera {args.camera}. Close EyeTrackVR / OBS and try again.")

    log = None
    if args.csv:
        log_file = open(args.csv, "w", newline="")
        log = csv.writer(log_file)
        log.writerow([f.name for f in fields(PupilSample)] + ["usable", "closed", "reason", "event"])

    validity = EyeValidity()
    last_event, last_event_until = None, 0.0
    fps, last_t = 0.0, None
    show_mask = False
    spread_until, spread_points, spread_total = None, [], 0
    message, message_until = "", 0.0
    print(__doc__.split("Keys:")[1])

    while True:
        ok, frame = cap.read()
        if not ok:
            message, message_until = "no frame from camera", time.monotonic() + 1
            if cv2.waitKey(100) & 0xFF in (ord("q"), 27):
                break
            continue
        t = time.monotonic()
        if last_t is not None:
            fps = 0.9 * fps + 0.1 / max(t - last_t, 1e-3)
        last_t = t

        sample = detector.process(frame, t)
        state, events = validity.update(sample)
        for event in events:
            print(f"{event.kind.upper():6s} blink  {event.duration * 1000:4.0f} ms  gaze before {event.gaze_before}")
            if event.kind != "blink" or last_event is None or t >= last_event_until:
                last_event, last_event_until = event, t + 1.5
        if log:
            row = [f"{v:.4f}" if isinstance(v, float) else v for v in asdict(sample).values()]
            log.writerow(row + [state.usable, state.closed, state.reason, "|".join(e.kind for e in events)])

        if spread_until is not None:
            spread_total += 1
            if state.usable:
                spread_points.append((sample.x, sample.y))
            if t >= spread_until:
                message = spread_report(spread_points, spread_total)
                message_until = t + 6
                print(message)
                spread_until = None

        # Draw
        view = frame.copy() if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        if show_mask and detector.debug_mask is not None:
            mask = cv2.cvtColor(detector.debug_mask, cv2.COLOR_GRAY2BGR)
            x0, y0 = (config.roi[0], config.roi[1]) if config.roi else (0, 0)
            view[y0 : y0 + mask.shape[0], x0 : x0 + mask.shape[1]] = mask
        view = cv2.resize(view, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_NEAREST)
        s = args.scale
        if config.roi:
            rx, ry, rw, rh = config.roi
            cv2.rectangle(view, (rx * s, ry * s), ((rx + rw) * s, (ry + rh) * s), (255, 200, 0), 1)
        if sample.valid:
            ring = (0, 255, 0) if state.usable else (0, 200, 255)  # yellow = seen but not trusted
            cv2.ellipse(
                view,
                ((sample.x * s, sample.y * s), (sample.minor * s, sample.major * s), sample.angle),
                ring,
                2,
            )
            cv2.drawMarker(view, (int(sample.x * s), int(sample.y * s)), ring, cv2.MARKER_CROSS, 12, 1)

        if state.closed:
            label, colour = ("CLOSED", (0, 200, 255)) if validity.closed_for(t) < validity.config.max_blink_s else ("LOST", (0, 0, 255))
        elif state.usable:
            label, colour = "TRACKING", (0, 255, 0)
        else:
            label, colour = f"UNTRUSTED ({state.reason})", (0, 200, 255)  # small/flat = lid or extreme angle
        lines = [
            f"{label}  {fps:4.1f} fps  contrast {config.min_contrast}",
            f"x {sample.x:6.1f}  y {sample.y:6.1f}  open {sample.openness:4.2f}" if sample.valid else "x  --    y  --",
        ]
        if spread_until is not None:
            lines.append(f"SPREAD TEST: hold still... {spread_until - t:.1f}s")
        elif t < message_until:
            lines.append(message)
        for i, line in enumerate(lines):
            cv2.putText(view, line, (8, 22 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour if i == 0 else (255, 255, 255), 1)
        if last_event and t < last_event_until:
            text = f"{last_event.kind.upper()} BLINK  {last_event.duration * 1000:.0f} ms"
            cv2.putText(view, text, (8, view.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.9, EVENT_COLOURS[last_event.kind], 2)
        cv2.imshow(WINDOW, view)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            break
        if key == ord("r"):
            box = cv2.selectROI(WINDOW, view, showCrosshair=False)
            if box[2] > 0 and box[3] > 0:
                config.roi = tuple(int(v / s) for v in box)
                detector.reset()
        elif key == ord("]"):
            config.min_contrast = min(config.min_contrast + 5, 150)
        elif key == ord("["):
            config.min_contrast = max(config.min_contrast - 5, 5)
        elif key == ord("m"):
            show_mask = not show_mask
        elif key == ord("s"):
            spread_until, spread_points, spread_total = t + SPREAD_SECONDS, [], 0
        elif key == ord("w"):
            CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2))
            message, message_until = f"saved {CONFIG_PATH.name}", t + 3

    cap.release()
    cv2.destroyAllWindows()
    if log:
        log_file.close()


if __name__ == "__main__":
    main()
