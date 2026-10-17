"""Gaze calibration: pupil (eye camera) -> front-camera pixels, using on-screen targets.

A full-screen window shows a black disc at 9 spots on the laptop screen. You
keep your head still and look at the red dot in the middle of each one; the
front camera on the glasses finds the disc (gaze/target.py - ArUco markers are
too small to decode at its 240x240), the eye camera finds the pupil, and the
pairs are fitted (gaze/mapper.py). No printed targets needed.

Usage (from the armproject folder, glasses on, laptop on the hotspot):
    calib.cmd                                   # eye camera 3, front camera Board B
    calib.cmd --eye 3 --scene http://172.20.10.2/
    calib.cmd --scene 1                         # a USB camera as the front camera

Keys: SPACE start / continue, r redo the last point, Esc quit.
Result: gaze/calib.json, plus the leave-one-out error in front-camera pixels.
"""

from __future__ import annotations

import argparse
import ctypes
import time

import cv2
import numpy as np

from eye.detector import PupilDetector
from eye.live import load_config
from eye.validity import EyeValidity
from gaze.capture import CameraThread
from gaze.mapper import CALIB_PATH, fit
from gaze.target import find_target, target_disc

WINDOW = "gaze calibration"
SETTLE_S = 1.0  # time to move the eyes to the new target
COLLECT_S = 1.2
MIN_EYE = 15  # usable pupil samples needed per point
MIN_SCENE = 3  # front-camera detections needed per point
GRID = [(0.5, 0.5), (0.2, 0.2), (0.5, 0.2), (0.8, 0.2), (0.8, 0.5), (0.8, 0.8), (0.5, 0.8), (0.2, 0.8), (0.2, 0.5)]  # clear of the bezel


def screen_size() -> tuple[int, int]:
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080




def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eye", default="3", help="Eye camera number")
    parser.add_argument("--scene", default="http://172.20.10.2/", help="Front camera URL or number")
    parser.add_argument("--marker", type=float, default=0.22, help="Target disc size as a fraction of screen height")
    args = parser.parse_args()

    sw, sh = screen_size()
    msize = int(sh * args.marker)
    marker = target_disc(msize)
    detector = PupilDetector(load_config())
    validity = EyeValidity()

    eye_cam = CameraThread(args.eye, "eye")
    scene_cam = CameraThread(args.scene, "front")
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    eye_count = scene_count = 0
    eye_ok = scene_seen = False
    scene_size = (0, 0)
    pupil_pts, scene_pts = [], []
    i, phase, phase_t = 0, "intro", time.monotonic()
    eye_buf, scene_buf, retries = [], [], 0
    result_lines: list[str] = []

    try:
        while True:
            now = time.monotonic()
            frame, _, count = eye_cam.latest()
            if frame is not None and count != eye_count:
                eye_count = count
                state, _ = validity.update(detector.process(frame, now))
                eye_ok = state.usable
                if phase == "collect" and state.usable:
                    eye_buf.append((state.sample.x, state.sample.y))
            frame, _, count = scene_cam.latest()
            if frame is not None and count != scene_count:
                scene_count = count
                scene_size = (frame.shape[1], frame.shape[0])
                centre = find_target(frame)
                scene_seen = centre is not None
                if scene_seen and phase == "collect":
                    scene_buf.append(np.array(centre))

            # Phase changes
            if phase == "settle" and now - phase_t >= SETTLE_S:
                phase, phase_t, eye_buf, scene_buf = "collect", now, [], []
            elif phase == "collect" and now - phase_t >= COLLECT_S:
                if len(eye_buf) >= MIN_EYE and len(scene_buf) >= MIN_SCENE:
                    pupil_pts.append(np.median(np.array(eye_buf), axis=0))
                    scene_pts.append(np.median(np.array(scene_buf), axis=0))
                    print(f"point {i + 1}/{len(GRID)}: pupil {pupil_pts[-1].round(1)}  front {scene_pts[-1].round(1)}  "
                          f"({len(eye_buf)} eye / {len(scene_buf)} front samples)")
                    i, retries = i + 1, 0
                else:
                    print(f"point {i + 1}: not enough data ({len(eye_buf)} eye, {len(scene_buf)} front) - retrying")
                    retries += 1
                    if retries > 2:
                        print(f"point {i + 1}: skipped")
                        i, retries = i + 1, 0
                if i >= len(GRID):
                    phase = "done"
                    if len(pupil_pts) >= 3:
                        calib = fit(np.array(pupil_pts), np.array(scene_pts), scene_size)
                        calib.save()
                        result_lines = [
                            f"Saved {CALIB_PATH.name}: {len(pupil_pts)} points",
                            f"Leave-one-out error: RMS {calib.loo_rms_px:.1f} px, worst {calib.loo_worst_px:.1f} px"
                            f" (front camera {scene_size[0]}x{scene_size[1]})",
                        ]
                    else:
                        result_lines = [f"Only {len(pupil_pts)} usable points - check both cameras and try again."]
                    for line in result_lines:
                        print(line)
                else:
                    phase, phase_t = "settle", now

            # Draw
            canvas = np.full((sh, sw, 3), 255, np.uint8)
            if phase in ("settle", "collect"):
                cx = min(max(int(GRID[i][0] * sw), msize // 2), sw - msize // 2)  # keep the disc on screen
                cy = min(max(int(GRID[i][1] * sh), msize // 2), sh - msize // 2)
                x0, y0 = cx - msize // 2, cy - msize // 2
                canvas[y0 : y0 + msize, x0 : x0 + msize] = marker
                if phase == "collect":
                    cv2.circle(canvas, (cx, cy), msize // 2 + 20, (0, 180, 0), 4)
            lines = {
                "intro": ["Gaze calibration - keep your head still.", "Look at the RED DOT in the middle of each square.",
                          "SPACE to start, Esc to quit."],
                "done": result_lines + ["SPACE / Esc to close."],
            }.get(phase, [f"Point {i + 1}/{len(GRID)} - look at the red dot"])
            for k, line in enumerate(lines):
                cv2.putText(canvas, line, (40, 60 + 45 * k), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (40, 40, 40), 2)
            status = f"eye: {'OK' if eye_ok else 'NO PUPIL'}   front camera: {'sees target' if scene_seen else 'no target'}"
            cv2.putText(canvas, status, (40, sh - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                        (0, 150, 0) if eye_ok and (scene_seen or phase in ("intro", "done")) else (0, 0, 200), 2)
            cv2.imshow(WINDOW, canvas)

            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                break
            if key == ord(" "):
                if phase == "intro":
                    phase, phase_t = "settle", time.monotonic()
                elif phase == "done":
                    break
            elif key == ord("r") and phase in ("settle", "collect") and pupil_pts:
                pupil_pts.pop(), scene_pts.pop()
                i, phase, phase_t = max(i - 1, 0), "settle", time.monotonic()
    finally:
        eye_cam.close()
        scene_cam.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
