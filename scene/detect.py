"""Find objects on the white board from the fixed side camera: where they are, which way they lie.

Anything clearly coloured inside the board is an object; the white board, the white/black arm,
the black markers and grey shadows are all low-saturation, so they drop out. Each blob is moved
into table millimetres through the marker homography and boxed there, so the angle and size are
in the table frame. Clear or white things are invisible to this - pick coloured objects.

Per-class knowledge lives in gaze/classes.json (written by scripted.cmd teach):
    {"granola_bar": {"name": "granola", "prompt": "granola bar", "grasp_mm": 19,
                     "taught": {"x": 151, "y": 116, "yaw": 170.8, "t": 1760710000.0},
                     "grip_offset": 82.3, "pos_offset": [4, -21], "hue": [52, 160], "learned_t": ...}}
grip_offset (jaw heading minus the object's long axis) and pos_offset (the taught spot minus where
the camera sees the object - the camera views raised objects at a slant) are learned the first time
the camera sees a taught object still at its taught spot, within a few minutes of the teach
(or on request, with the object put back on its taught spot).
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from scene.markers import TableConfig, pixels_to_table, table_to_pixels

ROOT = Path(__file__).resolve().parents[1]
CLASSES_PATH = ROOT / "gaze" / "classes.json"
SAT_MIN = 80  # HSV saturation (0-255) that counts as "coloured"
VAL_MIN = 40
MIN_AREA_MM2 = 250  # ignore specks smaller than ~1.6 cm square
MATCH_MM = 50  # a blob this close to a track is the same object
FORGET_S = 10.0  # keep an unseen object this long (the arm hides it while hovering)
LEARN_RADIUS_MM = 60  # a taught object counts as still at its taught spot within this
LEARN_WINDOW_S = 180  # learn only this soon after a teach (or when asked): later, something else may sit there
DEFAULT_GRIP_OFFSET = 90.0  # jaws close across the long axis until a teach says otherwise
DEFAULT_GRASP_MM = 15


def load_classes() -> dict:
    return json.loads(CLASSES_PATH.read_text()) if CLASSES_PATH.exists() else {}


def save_classes(classes: dict) -> None:
    _write_json(CLASSES_PATH, classes)


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    for _ in range(20):  # a reader may have the file open for a moment
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.05)


def prompt_of(cls: str, info: dict) -> str:
    return info.get("prompt") or cls.replace("_", " ")


def wrap180(a: float) -> float:
    """Angle into (-90, 90]: a gripper's jaws (and a box's axis) are the same turned half a turn."""
    a = (a + 90) % 180 - 90
    return 90.0 if a == -90 else a


@dataclass
class Blob:
    px: tuple[float, float]  # centroid, image pixels
    x: float  # table mm
    y: float
    axis: float  # long-axis heading in the table frame, degrees in (-90, 90]
    length: float  # mm
    width: float
    hue: float  # OpenCV hue 0-180 (circular mean)
    sat: float
    rect_mm: np.ndarray  # 4x2 box corners, table mm


def board_region(config: TableConfig) -> np.ndarray:
    """Table-mm polygon to look for objects in: the marker rectangle plus a small margin."""
    xs = [p[0] for p in config.markers.values()]
    ys = [p[1] for p in config.markers.values()]
    x0, x1, y0, y1 = min(xs) - 25, max(xs) + 25, min(ys) - 15, max(ys) + 25
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)


def find_blobs(frame: np.ndarray, H: np.ndarray, config: TableConfig) -> list[Blob]:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 1] >= SAT_MIN) & (hsv[..., 2] >= VAL_MIN)).astype(np.uint8) * 255
    region = np.zeros_like(mask)
    poly = np.clip(table_to_pixels(H, board_region(config)), -4000, 4000)  # a corner past the horizon flies off
    cv2.fillPoly(region, [poly.astype(np.int32)], 255)
    mask &= region
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    blobs = []
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    for c in contours:
        if cv2.contourArea(c) < 40:
            continue
        pts = pixels_to_table(H, c.reshape(-1, 2).astype(np.float32))
        area = cv2.contourArea(pts)
        if area < MIN_AREA_MM2:
            continue
        (cx, cy), (w, h), ang = cv2.minAreaRect(pts)
        if w < h:
            w, h, ang = h, w, ang + 90
        one = np.zeros_like(mask)
        cv2.drawContours(one, [c], -1, 255, -1)
        hues = hsv[..., 0][one > 0].astype(np.float32) * (2 * math.pi / 180)
        hue = (math.degrees(math.atan2(np.sin(hues).mean(), np.cos(hues).mean())) / 2) % 180
        m = cv2.moments(c)
        blobs.append(Blob((m["m10"] / m["m00"], m["m01"] / m["m00"]), float(cx), float(cy), wrap180(ang),
                          float(w), float(h), hue, float(hsv[..., 1][one > 0].mean()),
                          cv2.boxPoints(((cx, cy), (w, h), ang))))
    return blobs


@dataclass
class Track:
    id: int
    x: float
    y: float
    axis: float
    length: float
    width: float
    hue: float
    px: tuple[float, float]
    rect_mm: np.ndarray
    last_seen: float
    cls: str | None = None
    label_conf: float = 0.0  # best name confidence so far (decays, so a better label can take over)
    taught: bool = False  # named by a teach: never renamed by YOLO or colour
    hits: int = 1


def _hue_dist(a: float, b: float) -> float:
    d = abs(a - b) % 180
    return min(d, 180 - d)


@dataclass
class ObjectTracker:
    tracks: list[Track] = field(default_factory=list)
    _next: int = 1

    def update(self, blobs: list[Blob], now: float) -> None:
        free = list(self.tracks)
        for b in sorted(blobs, key=lambda b: -b.length * b.width):
            best = min(free, key=lambda t: math.hypot(t.x - b.x, t.y - b.y), default=None)
            if best is not None and math.hypot(best.x - b.x, best.y - b.y) <= MATCH_MM:
                free.remove(best)
                a = 0.4  # smoothing
                best.x += a * (b.x - best.x)
                best.y += a * (b.y - best.y)
                best.axis = wrap180(best.axis + a * wrap180(b.axis - best.axis))
                best.length += a * (b.length - best.length)
                best.width += a * (b.width - best.width)
                best.hue, best.px, best.rect_mm, best.last_seen = b.hue, b.px, b.rect_mm, now
                best.hits += 1
            else:
                self.tracks.append(Track(self._next, b.x, b.y, b.axis, b.length, b.width, b.hue, b.px, b.rect_mm, now))
                self._next += 1
        self.tracks = [t for t in self.tracks if now - t.last_seen <= FORGET_S]

    def label(self, names: list[tuple[str, float, tuple]], prompt_to_cls: dict[str, str], now: float) -> None:
        """Give tracks the YOLO name whose box holds their centre (seen this frame only)."""
        for t in self.tracks:
            t.label_conf *= 0.98
        for prompt, conf, (x1, y1, x2, y2) in names:
            inside = [t for t in self.tracks if t.last_seen == now and x1 <= t.px[0] <= x2 and y1 <= t.px[1] <= y2]
            for t in inside:
                if not t.taught and conf > t.label_conf:
                    t.cls, t.label_conf = prompt_to_cls[prompt], conf

    def colour_fallback(self, classes: dict, max_hue: float = 12.0) -> None:
        """Tracks YOLO hasn't named well: the class whose learned colour is closest, if close enough."""
        for t in self.tracks:
            if t.taught or t.label_conf >= 0.3:
                continue
            near = [(c, _hue_dist(t.hue, info["hue"][0])) for c, info in classes.items() if info.get("hue")]
            near = [cn for cn in near if cn[1] <= max_hue]
            if near:
                t.cls = min(near, key=lambda cn: cn[1])[0]

    def learn(self, classes: dict, now: float) -> list[str]:
        """Taught objects still at their taught spot: learn grip offset, position offset and colour."""
        learned = []
        for cls, info in classes.items():
            taught = info.get("taught")
            if not taught or info.get("learned_t", 0) >= taught["t"]:
                continue
            if now - taught["t"] > LEARN_WINDOW_S and not info.get("learn_now"):
                continue
            near = [t for t in self.tracks if t.last_seen == now and t.hits >= 3]
            t = min(near, key=lambda t: math.hypot(t.x - taught["x"], t.y - taught["y"]), default=None)
            if t is None or math.hypot(t.x - taught["x"], t.y - taught["y"]) > LEARN_RADIUS_MM:
                continue
            info["pos_offset"] = [round(taught["x"] - t.x, 1), round(taught["y"] - t.y, 1)]
            info["grip_offset"] = round(wrap180(taught["yaw"] - t.axis), 1)
            info["hue"] = [round(t.hue, 1), 0]
            info["learned_t"] = now
            info.pop("learn_now", None)
            t.cls, t.taught = cls, True
            learned.append(cls)
        return learned


def scene_objects(tracks: list[Track], classes: dict) -> list[dict]:
    """objects.json entries for the arm and the gaze selector."""
    out, used = [], {}
    for t in sorted(tracks, key=lambda t: t.id):
        info = classes.get(t.cls, {}) if t.cls else {}
        base = info.get("name", t.cls) if t.cls else "object"
        used[base] = used.get(base, 0) + 1
        name = base if used[base] == 1 else f"{base}_{used[base]}"
        dx, dy = info.get("pos_offset") or (0.0, 0.0)
        offset = info.get("grip_offset")
        out.append({"name": name, "cls": t.cls or "unknown", "x": round(t.x + dx), "y": round(t.y + dy),
                    "yaw": round(wrap180(t.axis + (DEFAULT_GRIP_OFFSET if offset is None else offset)), 1),
                    "grasp_mm": info.get("grasp_mm", DEFAULT_GRASP_MM),
                    "axis": round(t.axis, 1), "size_mm": [round(t.length), round(t.width)],
                    "seen_s_ago": round(time.time() - t.last_seen, 1) if t.last_seen > 1e9 else 0})
    return out
