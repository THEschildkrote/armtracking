"""Live object detection on the fixed side camera -> gaze/objects.json (positions, angles, names).

    objects.cmd           (q or Esc to quit; it owns the side camera, so close side_check first)

Every frame: lock the table from the markers, find coloured blobs on the board, follow them.
A few times a second YOLOE names them from the prompts in gaze/classes.json; objects it
can't name confidently fall back to the colour learned from a teach. Twice a second the
scene is written to gaze/objects.json, which the picks and the gaze selector read.

Teach an object once (scripted.cmd teach granola) with this running, move the arm out of the
way and leave the object on its spot: within 3 minutes it learns that object's colour, grip angle
and position correction. L relearns from every taught object (put them back on their taught spots).
The board is shaded by where the arm can pick (green: any angle, yellow: some, red: none; M toggles),
and objects it can't pick where they lie are boxed red, "OUT OF REACH".
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from arm import cameras as armcams  # noqa: E402
from scene.detect import (CLASSES_PATH, MIN_HITS, ObjectTracker, _write_json, find_blobs, load_classes, prompt_of,  # noqa: E402
                          save_classes, scene_objects)
from scene.markers import TableConfig, TableTracker, table_to_pixels  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OBJECTS_PATH = ROOT / "gaze" / "objects.json"
LOGS = ROOT / "logs"
NAME_EVERY_S = 0.3
WRITE_EVERY_S = 0.5


def open_side_camera():
    devices = armcams.video_devices() or armcams.video_devices()
    cfg = armcams.load()["front"]  # the arm config's "front" camera is the side camera
    idx = {usb: i for i, (_, usb) in enumerate(devices)}.get(cfg.get("usb_id"))
    if idx is None:
        raise SystemExit(f"side camera ({cfg.get('usb_id')}) not plugged in")
    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(cfg.get("rotation", 0))
    return cap, rot


class ReachWatcher:
    """In the background: can the arm pick each listed object where it is now, at its angle?"""

    def __init__(self):
        import threading

        from arm.reach import Reach, ReachMap
        from scene.detect import board_region

        self.reach = Reach()
        self.map = ReachMap(self.reach, board_region(TableConfig.load()))
        self.objects: list[dict] = []
        self.status: dict[str, tuple] = {}  # name -> (x, y, yaw, reachable)
        threading.Thread(target=self._run, daemon=True).start()

    def get(self, o: dict):
        st = self.status.get(o["name"])
        if st and abs(st[0] - o["x"]) <= 15 and abs(st[1] - o["y"]) <= 15 and abs(((st[2] - o["yaw"]) + 90) % 180 - 90) <= 10:
            return st[3]
        return None  # not checked at this spot yet

    def _run(self) -> None:
        while True:
            pending = [o for o in list(self.objects) if self.get(o) is None]
            if not pending:
                time.sleep(0.3)
                continue
            for o in pending:
                self.status[o["name"]] = (o["x"], o["y"], o["yaw"], self.reach.can_pick(o))


def draw_reach_map(view, H, rmap) -> None:
    overlay = view.copy()
    h = rmap.step / 2
    for (i, j), share in list(rmap.cells.items()):
        x, y = rmap.centre(i, j)
        cell = np.array([[x - h, y - h], [x + h, y - h], [x + h, y + h], [x - h, y + h]], np.float32)
        colour = (0, 180, 0) if share >= 1 else (0, 200, 255) if share > 0 else (0, 0, 220)
        cv2.fillPoly(overlay, [table_to_pixels(H, cell).astype(np.int32)], colour)
    cv2.addWeighted(overlay, 0.22, view, 0.78, 0, view)


def draw(view, H, tracks, objects, status_lines):
    listed = [t for t in sorted(tracks, key=lambda t: t.id) if t.hits >= MIN_HITS]
    for t, o in zip(listed, objects):
        box = table_to_pixels(H, t.rect_mm).astype(np.int32)
        named = o["cls"] != "unknown"
        reach = o.get("reachable")
        colour = (0, 0, 255) if reach is False else (0, 220, 0) if named else (0, 200, 255)
        cv2.polylines(view, [box], True, colour, 2)
        cx, cy = (int(v) for v in t.px)
        label = f"{o['name']} ({o['x']}, {o['y']}) {o['axis']:.0f}deg" +             ("  OUT OF REACH" if reach is False else "" if reach else "  (checking reach)")
        cv2.putText(view, label, (cx - 60, cy - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(view, label, (cx - 60, cy - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
    for i, text in enumerate(status_lines):
        cv2.putText(view, text, (10, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
        cv2.putText(view, text, (10, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)


def main() -> None:
    from scene.naming import Namer

    classes = load_classes()
    prompts = [prompt_of(c, info) for c, info in classes.items()]
    prompt_to_cls = {prompt_of(c, info): c for c, info in classes.items()}
    print("Loading YOLOE for:", prompts, flush=True)
    namer = Namer(prompts) if prompts else None
    classes_mtime = os.path.getmtime(CLASSES_PATH) if CLASSES_PATH.exists() else 0

    config = TableConfig.load()
    table = TableTracker(config, static=True, hold_path=LOGS / "side_table_hold.json")
    tracker = ObjectTracker()
    try:
        reach = ReachWatcher()
    except FileNotFoundError:
        reach = None
        print("No arm/table_to_robot.json - reach not shown (run scripted.cmd calibrate)", flush=True)
    show_map = True
    cap, rot = open_side_camera()
    LOGS.mkdir(exist_ok=True)
    last_name = last_write = last_snap = 0.0
    names: list = []
    print("Running. q to quit, L to relearn from taught objects.", flush=True)
    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.05)
            continue
        if rot is not None:
            frame = cv2.rotate(frame, rot)
        now = time.time()
        if CLASSES_PATH.exists() and os.path.getmtime(CLASSES_PATH) != classes_mtime:  # a teach just ran
            classes_mtime = os.path.getmtime(CLASSES_PATH)
            fresh = load_classes()
            if set(fresh) != set(classes):
                print("New classes taught - restart objects.cmd so YOLO looks for them too", flush=True)
            classes = fresh
        s = table.process(frame, now)
        status = [f"table {'LOCKED' if s.valid else 'NOT locked'}  markers {sorted(s.marker_px)}"
                  + (f"  fit {s.reproj_mm:.1f} mm" if s.valid else "")]
        objects = []
        if s.valid:
            tracker.update(find_blobs(frame, s.H, config), now)
            if namer and now - last_name >= NAME_EVERY_S:
                names = namer(frame)
                tracker.label(names, prompt_to_cls, now)
                last_name = now
            tracker.colour_fallback(classes)
            learned = tracker.learn(classes, now)
            if learned:
                save_classes(classes)
                classes_mtime = os.path.getmtime(CLASSES_PATH)
                for c in learned:
                    print(f"Learned {c}: grip offset {classes[c]['grip_offset']} deg, position correction "
                          f"{classes[c]['pos_offset']} mm, colour hue {classes[c]['hue'][0]}", flush=True)
            objects = scene_objects(tracker.tracks, classes)
            if reach:
                reach.objects = objects
                for o in objects:
                    o["reachable"] = reach.get(o)
            if now - last_write >= WRITE_EVERY_S:
                data = json.loads(OBJECTS_PATH.read_text()) if OBJECTS_PATH.exists() else {}
                data["objects"] = objects
                data["live"] = {"t": round(now, 2), "camera": "side"}
                _write_json(OBJECTS_PATH, data)
                last_write = now
            status.append(f"{len(objects)} objects: " + ", ".join(o["name"] for o in objects))
        else:
            status.append("objects.json not updated until the table is locked")
        view = frame.copy()
        for corners in s.marker_px.values():
            cv2.polylines(view, [corners.astype(np.int32)], True, (0, 255, 0), 1)
        if s.valid:
            if reach and show_map:
                draw_reach_map(view, s.H, reach.map)
                if not reach.map.done:
                    status.append(f"mapping reach {len(reach.map.cells)}/{reach.map.nx * reach.map.ny}"
                                  " (green: any angle, yellow: some, red: none)  M hides it")
            draw(view, s.H, tracker.tracks, objects, status)
        else:
            draw(view, None, [], [], status)
        if now - last_snap > 1.0:  # copies others can look at while this holds the camera
            cv2.imwrite(str(LOGS / "side_check.jpg"), frame)
            cv2.imwrite(str(LOGS / "live_objects_view.jpg"), view)
            (LOGS / "live_objects.txt").write_text("\n".join(status) + "\n" + json.dumps(objects, indent=1))
            last_snap = now
        cv2.imshow("objects (side camera)", view)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        if key in (ord("m"), ord("M")):
            show_map = not show_map
        if key in (ord("l"), ord("L")):
            for info in classes.values():
                if info.get("taught"):
                    info.pop("learned_t", None)
                    info["learn_now"] = True
            print("Relearning from taught objects...", flush=True)
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
