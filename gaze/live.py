"""Live gaze on the front camera: table mm, fixations, object selection, blink commands.

Eye camera -> pupil -> gaze/calib.json -> front-camera pixel -> table markers ->
table mm -> fixation (dwell) -> object candidate -> long blink = SELECTED.

Objects (names + table-mm centres) come from gaze/objects.json; table markers
from scene/config.json. Selections print as "SELECTED <name>" lines.

Usage (from the armproject folder):
    gaze.cmd                                   # eye camera 3, front camera Board B
    gaze.cmd --eye 3 --scene http://172.20.10.2/
    gaze.cmd --arm                             # a long-blink selection makes the follower pick it
    gaze.cmd --blink-action wave               # test: any long blink makes the arm wave

Keys: q/Esc quit.
"""

from __future__ import annotations

import argparse
import math
import time

import cv2
import numpy as np

from eye.detector import PupilDetector
from eye.live import load_config
from eye.validity import EyeValidity
from gaze.capture import CameraThread
from gaze.fixation import FixationDetector
from gaze.mapper import CALIB_PATH, GazeCalibration
from gaze.selection import Selector, load_objects
from scene.markers import TableConfig, TableTracker, pixels_to_table, table_to_pixels

WINDOW = "gaze"
SMOOTHING = 0.5  # display-only EMA for the raw dot when the table is not in view
EVENT_COLOURS = {"selected": (0, 220, 0), "ambiguous": (0, 140, 255), "cancelled": (255, 120, 255), "none": (160, 160, 160)}


def to_px(H, x_mm: float, y_mm: float, scale: float) -> tuple[int, int]:
    p = table_to_pixels(H, [[x_mm, y_mm]])[0] * scale
    return int(p[0]), int(p[1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eye", default="3")
    parser.add_argument("--scene", default="http://172.20.10.2/")
    parser.add_argument("--scale", type=float, default=0, help="Window zoom (default: fit about 720 px)")
    parser.add_argument("--arm", action="store_true", help="Send selections to the follower arm (arm/policies.json)")
    parser.add_argument("--blink-action", metavar="NAME",
                        help="Test mode: any long blink runs this arm/policies.json entry (e.g. wave) - "
                             "no table markers or object selection needed")
    args = parser.parse_args()
    runner = None
    if args.arm or args.blink_action:
        from arm.runner import ArmRunner

        runner = ArmRunner()

    if not CALIB_PATH.exists():
        raise SystemExit("No gaze/calib.json yet - run calib.cmd first.")
    calib = GazeCalibration.load()
    detector = PupilDetector(load_config())
    validity = EyeValidity()
    table = TableTracker(TableConfig.load())
    fixations = FixationDetector()
    objects = load_objects()
    selector = Selector(objects)
    eye_cam = CameraThread(args.eye, "eye")
    scene_cam = CameraThread(args.scene, "front")

    eye_count = scene_count = 0
    raw_uv: tuple[float, float] | None = None
    smooth_uv: tuple[float, float] | None = None
    scene_sample = None
    banner = None  # (SelectionEvent, until)
    print(f"Calibration from {calib.created}: LOO RMS {calib.loo_rms_px:.1f} px; objects: {[o.name for o in objects]}")

    def table_mm(u: float, v: float) -> tuple[float, float] | None:
        if scene_sample is None or scene_sample.H is None:
            return None
        x, y = pixels_to_table(scene_sample.H, [[u, v]])[0]
        return float(x), float(y)

    try:
        while True:
            now = time.monotonic()
            frame, _, count = eye_cam.latest()
            if frame is not None and count != eye_count:
                eye_count = count
                state, blinks = validity.update(detector.process(frame, now))
                mm = None
                if state.usable:
                    raw_uv = calib.map(state.sample.x, state.sample.y)
                    smooth_uv = raw_uv if smooth_uv is None else (
                        smooth_uv[0] + SMOOTHING * (raw_uv[0] - smooth_uv[0]),
                        smooth_uv[1] + SMOOTHING * (raw_uv[1] - smooth_uv[1]))
                    mm = table_mm(*raw_uv)
                else:
                    raw_uv = None
                fix = fixations.update(now, *(mm if mm else (math.nan, math.nan)), mm is not None)
                if fix:
                    ev = selector.on_fixation(now, fix.x, fix.y)
                    if ev.kind == "ambiguous":
                        banner = (ev, now + 1.5)
                for blink in blinks:
                    if blink.kind not in ("long", "double"):
                        continue
                    if args.blink_action:  # test mode: the blink itself is the command
                        if blink.kind == "long":
                            started = runner.request(args.blink_action)
                            print(f"LONG BLINK -> {args.blink_action}: {'started' if started else 'arm busy, ignored'}", flush=True)
                        continue
                    bx, by = math.nan, math.nan
                    if blink.gaze_before:
                        b_mm = table_mm(*calib.map(*blink.gaze_before))
                        if b_mm:
                            bx, by = b_mm
                    ev = selector.on_blink(now, blink.kind, bx, by)
                    if ev:
                        banner = (ev, now + 2.5)
                        where = f"at ({bx:.0f}, {by:.0f}) mm" if not math.isnan(bx) else "(table not in view)"
                        print(f"{ev.kind.upper()} {ev.name or ', '.join(ev.near)} {where}", flush=True)
                        if runner and ev.kind == "selected":
                            started = runner.request(ev.name)
                            print(f"ARM: {'started ' + ev.name if started else 'busy (' + runner.status + ') - ignored'}", flush=True)

            frame, _, count = scene_cam.latest()
            if frame is None:
                if cv2.waitKey(10) & 0xFF in (ord("q"), 27):
                    break
                continue
            if count != scene_count:
                scene_count = count
                scene_sample = table.process(frame, now)

            scale = args.scale or max(1.0, 720 / max(frame.shape[:2]))
            view = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            view = cv2.resize(view, None, fx=scale, fy=scale)
            H = scene_sample.H if scene_sample is not None else None
            lines = []

            if scene_sample is not None:
                for px in scene_sample.marker_px.values():
                    cv2.polylines(view, [(px * scale).astype(int)], True, (0, 255, 0), 1)
            if H is not None:
                for obj in objects:  # object spots; the current candidate is highlighted
                    p = to_px(H, obj.x, obj.y, scale)
                    hot = obj.name == selector.candidate
                    cv2.circle(view, p, 22 if hot else 16, (0, 255, 255) if hot else (200, 200, 200), 3 if hot else 1)
                    cv2.putText(view, obj.name, (p[0] + 18, p[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                if fixations.position:  # filtered gaze with the dwell ring filling up
                    p = to_px(H, *fixations.position, scale)
                    cv2.circle(view, p, 4, (0, 0, 255), -1)
                    cv2.ellipse(view, p, (14, 14), -90, 0, 360 * fixations.progress, (0, 0, 255), 2)
                    lines.append(f"gaze ({fixations.position[0]:.0f}, {fixations.position[1]:.0f}) mm"
                                 + (f"  -> {selector.candidate}" if selector.candidate else ""))
                else:
                    lines.append("gaze: --  (blink / no pupil)")
            else:
                lines.append("table markers not in view")
                if raw_uv and smooth_uv:
                    cv2.circle(view, (int(smooth_uv[0] * scale), int(smooth_uv[1] * scale)), 10, (0, 0, 255), 2)

            if banner and now < banner[1]:
                ev = banner[0]
                text = {"selected": f"SELECTED {ev.name}", "ambiguous": f"AMBIGUOUS: {' / '.join(ev.near)} - look again",
                        "cancelled": "CANCELLED", "none": "nothing there"}[ev.kind]
                cv2.putText(view, text, (8, view.shape[0] - 16), cv2.FONT_HERSHEY_SIMPLEX, 1.0, EVENT_COLOURS[ev.kind], 2)
                if H is not None and ev.kind == "selected":
                    obj = next(o for o in objects if o.name == ev.name)
                    cv2.circle(view, to_px(H, obj.x, obj.y, scale), 30, EVENT_COLOURS["selected"], 4)
            if scene_cam.age() > 1.0:
                lines.append(f"FRONT CAMERA STALLED - reconnecting ({scene_cam.reconnects} so far)")
            if runner:
                lines.append(f"ARM: {runner.status}")
            for k, line in enumerate(lines):
                cv2.putText(view, line, (8, 24 + 24 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.imshow(WINDOW, view)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        eye_cam.close()
        scene_cam.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
