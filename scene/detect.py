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
VAL_MIN = 25  # dark wrappers in the arm's shadow read ~45
MIN_AREA_MM2 = 250  # ignore specks smaller than ~1.6 cm square
MATCH_MM = 50  # a blob this close to a track is the same object
MERGE_MM = 35  # two tracks this close are one object seen twice
MIN_HITS = 8  # frames an object must be seen before it is listed (hands reaching in are brief)
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
    hist: np.ndarray | None = None  # colour mix: HUE_BINS-bin hue histogram, weighted by saturation, sums to 1


HUE_BINS = 18  # 10 OpenCV hue units each


def hue_hist(hues: np.ndarray, sats: np.ndarray) -> np.ndarray:
    h = np.bincount((hues.astype(np.int64) % 180) * HUE_BINS // 180, weights=sats.astype(np.float64), minlength=HUE_BINS)
    return h / max(h.sum(), 1e-9)


def hist_of(peaks: list[tuple[float, float]]) -> np.ndarray:
    """A colour mix from (hue, share) pairs - for classes not taught yet."""
    h = np.zeros(HUE_BINS)
    centres = (np.arange(HUE_BINS) + 0.5) * 180 / HUE_BINS
    for hue, share in peaks:
        d = np.minimum(np.abs(centres - hue), 180 - np.abs(centres - hue))
        g = np.exp(-0.5 * (d / 8.0) ** 2)
        h += share * g / g.sum()
    return h / h.sum()


def similarity(hist: np.ndarray, area: float, sig: dict) -> float:
    """How well a blob matches a class signature: shared colour mix, minus a size mismatch penalty."""
    colour = float(np.minimum(hist, np.asarray(sig["hist"])).sum())
    size = abs(math.log(max(area, 1.0) / sig["area_mm2"]))
    return colour - 0.6 * size


def class_signature(info: dict) -> dict | None:
    if info.get("sig"):
        return info["sig"]
    if info.get("hue") and info.get("area_mm2"):
        return {"hist": hist_of([(info["hue"][0], 1.0)]).tolist(), "area_mm2": info["area_mm2"]}
    return None


def main_hue(hues: np.ndarray) -> float:
    """The dominant hue (0-180) of a set of pixels: the peak of a smoothed, wrap-around histogram."""
    hist = np.bincount(hues.astype(np.int64) % 180, minlength=180).astype(np.float32)
    k = np.array([1, 2, 3, 2, 1], np.float32)
    smooth = np.convolve(np.r_[hist[-2:], hist, hist[:2]], k, mode="valid")
    return float(np.argmax(smooth))


def board_region(config: TableConfig) -> np.ndarray:
    """Table-mm polygon to look for objects in: the marker rectangle out to about the board's edge."""
    xs = [p[0] for p in config.markers.values()]
    ys = [p[1] for p in config.markers.values()]
    x0, x1, y0, y1 = min(xs) - 40, max(xs) + 40, min(ys) - 15, max(ys) + 80
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)


def find_blobs(frame: np.ndarray, H: np.ndarray, config: TableConfig) -> list[Blob]:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    region = np.zeros(frame.shape[:2], np.uint8)
    poly = np.clip(table_to_pixels(H, board_region(config)), -4000, 4000)  # a corner past the horizon flies off
    cv2.fillPoly(region, [poly.astype(np.int32)], 255)
    for mid, (cx, cy) in config.markers.items():  # the markers' black/white edges pick up colour fringes: skip them
        h = config.size_of(mid) / 2 + 12
        sq = np.array([[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]], np.float32)
        cv2.fillPoly(region, [table_to_pixels(H, sq).astype(np.int32)], 0)
    # the board's own colour under this light (it reads pinkish/yellowish, and shadows keep that hue)
    bright = (region > 0) & (val > 120)
    if bright.sum() < 500:
        return []
    board_hue = float(np.median(hue[bright]))
    board_sat = float(np.median(sat[bright]))
    hue_gap = np.abs(hue.astype(np.int16) - int(board_hue)) % 180
    hue_gap = np.minimum(hue_gap, 180 - hue_gap)
    coloured = (sat >= max(SAT_MIN, board_sat + 45)) & (val >= VAL_MIN)
    # Dark but only moderately saturated (a teal wrapper far from the camera reads sat ~60, val ~90): still an
    # object if it's clearly darker than the board AND its hue is far from the board's tint. The arm's black
    # parts and shadows sit close to the board's tint (~20 apart), so they stay out.
    board_val = float(np.median(val[bright]))
    coloured |= (val < 0.6 * board_val) & (sat >= 40) & (hue_gap > 30)
    board_like = (hue_gap <= 15) & (sat < board_sat + 90)  # a shadow or a tinted patch of board
    # only what sits on the white board: its outline (holes and all) rules out the table around it
    white = ((sat < board_sat + 30) & (val > 150) & (region > 0)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(white)
    if n < 2:
        return []
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    outline = cv2.findNonZero((labels == biggest).astype(np.uint8))
    on_board = np.zeros_like(white)
    cv2.fillConvexPoly(on_board, cv2.convexHull(outline), 1)
    mask = (coloured & ~board_like & (region > 0) & (on_board > 0)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # pieces of one object (wrapper colours, print) touch or nearly so: group them, measure the union
    groups = cv2.dilate(mask, np.ones((11, 11), np.uint8))
    contours, _ = cv2.findContours(groups, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pieces = []
    for c in contours:
        one = np.zeros_like(mask)
        cv2.drawContours(one, [c], -1, 255, -1)
        one &= mask
        if cv2.countNonZero(one) < 40:
            continue
        hs = hue[one > 0].astype(np.float32) * (2 * math.pi / 180)
        pieces.append([one, cv2.boundingRect(c), (math.degrees(math.atan2(np.sin(hs).mean(), np.cos(hs).mean())) / 2) % 180])
    merged = True
    while merged:
        merged = False
        for i in range(len(pieces)):
            for j in range(i + 1, len(pieces)):
                (_, (xa, ya, wa, ha), hua), (_, (xb, yb, wb, hb), hub) = pieces[i], pieces[j]
                overlap = min(xa + wa, xb + wb) - max(xa, xb)
                gap = max(ya, yb) - min(ya + ha, yb + hb)
                if overlap > 0.6 * min(wa, wb) and gap < 30 and _hue_dist(hua, hub) <= 8:
                    one = pieces[i][0] | pieces[j][0]
                    x0, y0 = min(xa, xb), min(ya, yb)
                    pieces[i] = [one, (x0, y0, max(xa + wa, xb + wb) - x0, max(ya + ha, yb + hb) - y0), hua]
                    del pieces[j]
                    merged = True
                    break
            if merged:
                break
    blobs = []
    for one, _, _ in pieces:
        pix = cv2.findNonZero(one)
        if pix is None or len(pix) < 40:
            continue
        pix = pix.reshape(-1, 2).astype(np.float32)
        pts = pixels_to_table(H, pix[:: max(1, len(pix) // 2000)])
        (cx, cy), (w, hh), ang = cv2.minAreaRect(pts)
        if w * hh < MIN_AREA_MM2:
            continue
        if w < hh:
            w, hh, ang = hh, w, ang + 90
        mean_hue = main_hue(hue[one > 0])
        u, v = pix.mean(axis=0)
        blobs.append(Blob((float(u), float(v)), float(cx), float(cy), wrap180(ang), float(w), float(hh), mean_hue,
                          float(sat[one > 0].mean()), cv2.boxPoints(((cx, cy), (w, hh), ang)),
                          hue_hist(hue[one > 0], sat[one > 0])))
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
    hist: np.ndarray | None = None  # smoothed colour mix
    votes: dict = field(default_factory=dict)  # class -> smoothed match score
    yolo: dict = field(default_factory=dict)  # class -> recent YOLO confidence


def _inside_fraction(small: Track, big: Track) -> float:
    """How much of small's box lies inside big's box (0..1), in table mm."""
    a = small.rect_mm.astype(np.float32)
    area = cv2.contourArea(a)
    if area <= 0:
        return 0.0
    inter, _ = cv2.intersectConvexConvex(a, big.rect_mm.astype(np.float32))
    return float(inter) / area


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
                d = ((b.hue - best.hue + 90) % 180) - 90  # circular
                best.hue = (best.hue + a * d) % 180
                best.px, best.rect_mm, best.last_seen = b.px, b.rect_mm, now
                if b.hist is not None:
                    best.hist = b.hist if best.hist is None else (1 - a) * best.hist + a * b.hist
                best.hits += 1
            else:
                self.tracks.append(Track(self._next, b.x, b.y, b.axis, b.length, b.width, b.hue, b.px, b.rect_mm, now,
                                         hist=b.hist))
                self._next += 1
        self.tracks = [t for t in self.tracks if now - t.last_seen <= FORGET_S]
        keep: list[Track] = []
        # duplicates (close together, or a piece lying mostly inside a bigger box): the bigger box wins
        for t in sorted(self.tracks, key=lambda t: (-min(t.hits, MIN_HITS), -(t.length * t.width))):
            if all(math.hypot(t.x - k.x, t.y - k.y) > MERGE_MM and _inside_fraction(t, k) < 0.3 for k in keep):
                keep.append(t)
        self.tracks = keep

    def label(self, names: list[tuple[str, float, tuple]], prompt_to_cls: dict[str, str], now: float) -> None:
        """Give tracks the YOLO name whose box holds their centre (seen this frame only)."""
        for t in self.tracks:
            t.yolo = {c: v * 0.9 for c, v in t.yolo.items()}
        for prompt, conf, (x1, y1, x2, y2) in names:
            for t in self.tracks:
                if t.last_seen == now and x1 <= t.px[0] <= x2 and y1 <= t.px[1] <= y2:
                    c = prompt_to_cls[prompt]
                    t.yolo[c] = max(t.yolo.get(c, 0.0), conf)

    def identify(self, classes: dict, now: float) -> None:
        """Name each track by colour mix and size (plus YOLO's hints), voted over frames so one odd frame -
        a hand passing, a reflection - doesn't rename it. Taught tracks keep their name."""
        sigs = {c: sig for c, info in classes.items() if (sig := class_signature(info))}
        for t in self.tracks:
            if t.taught or t.hist is None or t.last_seen != now:
                continue
            area = t.length * t.width
            for c in set(sigs) | set(t.yolo):
                score = (similarity(t.hist, area, sigs[c]) if c in sigs else 0.0) + 0.5 * t.yolo.get(c, 0.0)
                t.votes[c] = 0.85 * t.votes.get(c, score) + 0.15 * score
            best = max(t.votes, key=t.votes.get, default=None)
            t.cls = best if best is not None and t.votes[best] >= 0.2 else None

    def learn(self, classes: dict, now: float) -> list[str]:
        """Taught objects still at their taught spot: learn grip offset, position offset and colour."""
        learned = []
        for cls, info in classes.items():
            taught = info.get("taught")
            if not taught or info.get("learned_t", 0) >= taught["t"]:
                continue
            if now - taught["t"] > LEARN_WINDOW_S and not info.get("learn_now"):
                continue
            near = [t for t in self.tracks if t.last_seen == now and t.hits >= MIN_HITS
                    and math.hypot(t.x - taught["x"], t.y - taught["y"]) <= LEARN_RADIUS_MM]
            t = max(near, key=lambda t: t.length * t.width, default=None)
            if t is None:
                continue
            info["pos_offset"] = [round(taught["x"] - t.x, 1), round(taught["y"] - t.y, 1)]
            info["grip_offset"] = round(wrap180(taught["yaw"] - t.axis), 1)
            info["hue"] = [round(t.hue, 1), 0]
            if t.hist is not None:
                info["sig"] = {"hist": [round(float(v), 4) for v in t.hist], "area_mm2": round(t.length * t.width)}
            info["learned_t"] = now
            info.pop("learn_now", None)
            t.cls, t.taught = cls, True
            learned.append(cls)
        return learned


def scene_objects(tracks: list[Track], classes: dict) -> list[dict]:
    """objects.json entries for the arm and the gaze selector."""
    out, used = [], {}
    for t in sorted(tracks, key=lambda t: t.id):
        if t.hits < MIN_HITS:
            continue
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
