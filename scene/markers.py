"""Table tracking for the front (scene) camera with ArUco markers.

Four markers sit at known positions on the table. Every frame we find them and
fit a homography H: camera pixels -> table millimetres. Because it is refit per
frame, it keeps working while the head-mounted camera moves.

Table frame: origin and axes are whatever you measure the marker centres in,
e.g. origin at the mat's bottom-left corner, x to the right, y away from you.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

CONFIG_PATH = Path(__file__).with_name("config.json")
DICTIONARY = cv2.aruco.DICT_4X4_50


@dataclass
class TableConfig:
    marker_size_mm: float = 50.0  # printed black-square edge length
    # marker id -> centre (x, y) on the table in mm. Measure yours and edit config.json.
    markers: dict[int, tuple[float, float]] = field(
        default_factory=lambda: {0: (0.0, 0.0), 1: (400.0, 0.0), 2: (400.0, 300.0), 3: (0.0, 300.0)}
    )
    # marker id -> its own printed edge length (mm), for markers not marker_size_mm (the big head-camera ones)
    sizes: dict[int, float] = field(default_factory=dict)
    min_markers: int = 2  # each marker gives 4 corners, so 2 markers already fix a homography
    max_reproj_mm: float = 10.0  # reject a fit whose corners land further than this from where they belong

    @classmethod
    def load(cls) -> TableConfig:
        if not CONFIG_PATH.exists():
            return cls()
        data = json.loads(CONFIG_PATH.read_text())
        data["markers"] = {int(k): tuple(v) for k, v in data.get("markers", {}).items()}
        data["sizes"] = {int(k): float(v) for k, v in data.get("sizes", {}).items()}
        return cls(**data)

    def save(self) -> None:
        data = {**self.__dict__, "markers": {str(k): list(v) for k, v in self.markers.items()},
                "sizes": {str(k): v for k, v in self.sizes.items()}}
        CONFIG_PATH.write_text(json.dumps(data, indent=2))

    def size_of(self, marker_id: int) -> float:
        return self.sizes.get(marker_id, self.marker_size_mm)

    def corners_mm(self, marker_id: int) -> np.ndarray:
        """The 4 corners of a marker in table mm, in ArUco order (TL, TR, BR, BL as printed).

        Printed markers lie flat with their top edge pointing toward +y (away from you).
        """
        cx, cy = self.markers[marker_id]
        h = self.size_of(marker_id) / 2
        return np.array([[cx - h, cy + h], [cx + h, cy + h], [cx + h, cy - h], [cx - h, cy - h]], np.float32)


@dataclass
class SceneSample:
    t: float
    marker_px: dict[int, np.ndarray]  # id -> 4x2 corner pixels of every known marker seen
    H: np.ndarray | None  # 3x3, camera pixels -> table mm; None when not enough markers
    reproj_mm: float  # RMS corner error of the fit, mm (nan when no fit)
    valid: bool


class TableTracker:
    def __init__(self, config: TableConfig | None = None, static: bool = False, hold_path: Path | None = None,
                 memory_s: float = 0.0):
        """static=True for a fixed camera (the side camera): once all markers have been read in one
        frame, that fit is held while whatever markers are visible still agree with it, so small far
        markers flickering in and out don't matter. A moving (head) camera must refit every frame.
        hold_path keeps the held fit across restarts (still re-checked against the visible markers)."""
        self.config = config or TableConfig()
        self.static = static
        self.hold_path = hold_path
        # memory_s > 0 (head camera): a marker stays usable this long after it was last decoded. Board B decodes
        # the small markers only now and then, one at a time; while the head is near-still, markers seen a few
        # frames apart combine into a fit.
        self.memory_s = memory_s
        self._recent: dict[int, tuple[float, np.ndarray]] = {}
        self._held: np.ndarray | None = None
        self._saved_at = 0.0
        if static and hold_path and hold_path.exists():
            data = json.loads(hold_path.read_text())
            if data.get("markers") == {str(k): list(v) for k, v in self.config.markers.items()}:
                self._held = np.array(data["H"])
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX  # steadier corners on small markers
        self._detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICTIONARY), params)

    def _detect(self, gray: np.ndarray, scale: float) -> dict[int, np.ndarray]:
        img = gray if scale == 1 else cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        corners, ids, _ = self._detector.detectMarkers(img)
        seen: dict[int, np.ndarray] = {}
        if ids is not None:
            for marker_corners, marker_id in zip(corners, ids.flatten()):
                if int(marker_id) in self.config.markers:
                    seen[int(marker_id)] = marker_corners.reshape(4, 2) / scale
        return seen

    def process(self, frame: np.ndarray, t: float) -> SceneSample:
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        seen = self._detect(gray, 1)
        if len(seen) < len(self.config.markers):
            # small/far markers (a few px per cell): try again on an enlarged copy, keep whichever finds more
            bigger = self._detect(gray, 2)
            if len(bigger) > len(seen):
                seen = bigger

        if self.memory_s > 0:
            for mid, corners in seen.items():
                self._recent[mid] = (t, corners)
            self._recent = {m: v for m, v in self._recent.items() if t - v[0] <= self.memory_s}
            seen = {m: v[1] for m, v in self._recent.items()}

        if len(seen) < self.config.min_markers:
            # One marker can't tell a held fit from a slightly bumped camera (it misses small turns),
            # so it never confirms one: not locked until a second marker shows.
            return SceneSample(t, seen, None, float("nan"), False)

        px = np.concatenate([seen[i] for i in seen]).astype(np.float32)
        mm = np.concatenate([self.config.corners_mm(i) for i in seen])
        H, _ = cv2.findHomography(px, mm, 0)
        if H is None:
            return SceneSample(t, seen, None, float("nan"), False)
        err = np.linalg.norm(pixels_to_table(H, px) - mm, axis=1)
        rms = float(np.sqrt((err**2).mean()))
        ok = rms <= self.config.max_reproj_mm
        if not self.static:
            return SceneSample(t, seen, H, rms, ok)
        if ok and len(seen) == len(self.config.markers):
            if self.hold_path and (self._held is None or time.time() - self._saved_at > 10):
                self.hold_path.write_text(json.dumps({"H": H.tolist(), "rms_mm": rms,
                                                      "markers": {str(k): list(v) for k, v in self.config.markers.items()}}))
                self._saved_at = time.time()
            self._held = H  # the best kind of fit: hold it
            return SceneSample(t, seen, H, rms, True)
        if self._held is not None:
            held_err = np.linalg.norm(pixels_to_table(self._held, px) - mm, axis=1)
            held_rms = float(np.sqrt((held_err**2).mean()))
            if held_rms <= self.config.max_reproj_mm:
                return SceneSample(t, seen, self._held, held_rms, True)
            self._held = None  # the visible markers moved: the camera was bumped
        # a fixed camera's far markers are the small ones: two near markers alone extrapolate badly
        return SceneSample(t, seen, H, rms, ok and len(seen) >= 3)


def pixels_to_table(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Nx2 camera pixels -> Nx2 table mm."""
    return cv2.perspectiveTransform(np.asarray(pts, np.float32).reshape(-1, 1, 2), H).reshape(-1, 2)


def table_to_pixels(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Nx2 table mm -> Nx2 camera pixels."""
    return cv2.perspectiveTransform(np.asarray(pts, np.float32).reshape(-1, 1, 2), np.linalg.inv(H)).reshape(-1, 2)
