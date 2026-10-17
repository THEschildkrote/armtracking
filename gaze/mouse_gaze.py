"""Mouse as gaze: test the whole intent -> arm pipeline without the glasses.

    mouse_gaze.cmd                (objects.cmd should be running, so objects are live)

A top-down map of the board (table mm) with the objects from gaze/objects.json. The mouse
pointer is your gaze, fed to the same intent agent (BlinkShell) and real arm (IntentArm) that
gaze.cmd --intent uses:
    rest the pointer on an object  -> dwell -> the arm pre-hovers over it
    b                              -> long blink: authorizes the descent (needs a completed dwell)
    d                              -> double blink: cancel the pre-hover
    s or space                     -> STOP: the arm freezes where it is
    q / Esc                        -> quit
Every event goes to logs/session-*.jsonl like a real session.
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from gaze.intent_bridge import CONFIRM_DWELL_S, OBJECTS_PATH, RADIUS_MM, load_scene, make_intent  # noqa: E402
from scene.detect import board_region  # noqa: E402
from scene.markers import TableConfig  # noqa: E402

WINDOW = "mouse gaze (board, top-down)"
SCALE = 1.2  # px per mm
MARGIN = 40
LONG_BLINK_S = 0.6


def main() -> None:
    config = TableConfig.load()
    region = board_region(config)
    x0, y0 = region[:, 0].min() - 20, region[:, 1].min() - 60
    x1, y1 = region[:, 0].max() + 20, max(region[:, 1].max(), 360) + 20
    w, h = int((x1 - x0) * SCALE) + 2 * MARGIN, int((y1 - y0) * SCALE) + 2 * MARGIN + 90

    def px(x, y):
        return int(MARGIN + (x - x0) * SCALE), int(MARGIN + (y1 - y) * SCALE)

    def mm(u, v):
        return x0 + (u - MARGIN) / SCALE, y1 - (v - MARGIN) / SCALE

    shell, arm, log = make_intent(use_chalk=False)
    stop = threading.Event()
    agent = threading.Thread(target=shell.run, args=(stop, time.monotonic), daemon=True)
    agent.start()
    print(f"Intent agent + arm ready; logging to {log.path}", flush=True)

    mouse = {"uv": None}
    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, lambda ev, u, v, *_: mouse.__setitem__("uv", (u, v)))
    objects_mtime, data = 0.0, {"objects": []}
    blink_until, blink_at = 0.0, None
    banner, banner_until = "", 0.0
    try:
        while True:
            now = time.monotonic()
            try:  # follow objects.cmd
                mtime = OBJECTS_PATH.stat().st_mtime
                if mtime != objects_mtime:
                    objects_mtime, data = mtime, json.loads(OBJECTS_PATH.read_text())
                    shell.scene(load_scene(), now)
            except (OSError, ValueError, KeyError):
                pass

            gaze = mm(*mouse["uv"]) if mouse["uv"] else None
            on_board = gaze is not None and x0 <= gaze[0] <= x1 and y0 <= gaze[1] <= y1
            if blink_at is not None and now >= blink_until:  # the long blink ends: confirm
                shell.set_eyes_closed(False)
                picked = shell.blink_confirm(now, *blink_at)
                banner = f"LONG BLINK -> {'descend on ' + picked if picked else 'ignored (dwell on an object first)'}"
                banner_until, blink_at = now + 3, None
                print(banner, flush=True)
            elif blink_at is None:
                if on_board:
                    shell.sample(now, gaze[0], gaze[1])
                else:
                    shell.sample(now, math.nan, math.nan, False)

            view = np.full((h, w, 3), 40, np.uint8)
            cv2.rectangle(view, px(region[:, 0].min(), region[:, 1].max()), px(region[:, 0].max(), region[:, 1].min()),
                          (235, 235, 235), -1)
            for mid, (mx, my) in config.markers.items():
                cv2.rectangle(view, px(mx - 25, my + 25), px(mx + 25, my - 25), (0, 0, 0), -1)
                cv2.putText(view, str(mid), px(mx - 8, my - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            if data.get("drop"):
                d = data["drop"]
                cv2.circle(view, px(d["x"], d["y"]), 22, (200, 120, 0), 2)
                cv2.putText(view, "drop", px(d["x"] - 18, d["y"] - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 120, 0), 1)
            conf_id = shell.confirmable[0] if shell.confirmable else None
            for o in data.get("objects", []):
                length, width = o.get("size_mm", [40, 40])
                box = cv2.boxPoints(((o["x"], o["y"]), (length, width), o.get("axis", 0)))
                hot = o["name"] in (shell.dwelling, conf_id, arm.hover_id)
                colour = (0, 0, 220) if o.get("reachable") is False else (0, 170, 0) if hot else (90, 90, 90)
                cv2.polylines(view, [np.array([px(*p) for p in box], np.int32)], True, colour, 3 if hot else 2)
                cv2.circle(view, px(o["x"], o["y"]), int(RADIUS_MM * SCALE), colour, 1)
                tag = o["name"] + (" (out of reach)" if o.get("reachable") is False else "")
                cv2.putText(view, tag, px(o["x"] - 40, o["y"] + 45), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2)
            if on_board:
                cv2.circle(view, mouse["uv"], 6, (0, 0, 255), -1)
            lines = [
                f"gaze {'(%.0f, %.0f) mm' % gaze if on_board else '--'}   dwell on: {shell.dwelling or '-'}"
                f"   confirmable: {conf_id or '-'} (after {CONFIRM_DWELL_S}s dwell)",
                f"arm: {arm.status}   hovering over: {arm.hover_id or '-'}"
                + ("   [eyes closed]" if blink_at else ""),
                "b long blink   d double blink (cancel)   s STOP   q quit",
            ]
            if now < banner_until:
                lines.insert(0, banner)
            for i, text in enumerate(lines):
                cv2.putText(view, text, (12, h - 80 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)
            cv2.imshow(WINDOW, view)

            key = cv2.waitKey(30) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key == ord("b") and blink_at is None and on_board:
                shell.set_eyes_closed(True)  # holds the pre-hover through the blink, like the real one
                blink_at, blink_until = gaze, now + LONG_BLINK_S
            elif key == ord("d"):
                shell.cancel_all(now)
                banner, banner_until = "DOUBLE BLINK -> cancel pre-hover", now + 2
            elif key in (ord("s"), ord("S"), ord(" ")):
                from arm.motion import request_stop

                request_stop()
                shell.cancel_all(now)
                banner, banner_until = "STOP - arm frozen in place (home.cmd parks it)", now + 5
                print(banner, flush=True)
    finally:
        stop.set()
        agent.join(timeout=2)  # let it finish writing before the log closes
        log.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
