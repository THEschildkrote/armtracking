"""Table tracking for the front (scene) camera with ArUco markers.

Four markers sit at known positions on the table. Every frame we find them and
fit a homography H: camera pixels -> table millimetres. Because it is refit per
frame, it keeps working while the head-mounted camera moves.

Table frame: origin and axes are whatever you measure the marker centres in,
e.g. origin at the mat's bottom-left corner, x to the right, y away from you.
"""

from __future__ import annotations

import json
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
    min_markers: int = 2  # each marker gives 4 corners, so 2 markers already fix a homography
    max_reproj_mm: float = 10.0  # reject a fit whose corners land further than this from where they belong

    @classmethod
    def load(cls) -> TableConfig:
        if not CONFIG_PATH.exists():
            return cls()
        data = json.loads(CONFIG_PATH.read_text())
        data["markers"] = {int(k): tuple(v) for k, v in data.get("markers", {}).items()}
        return cls(**data)

    def save(self) -> None:
        data = {**self.__dict__, "markers": {str(k): list(v) for k, v in self.markers.items()}}
        CONFIG_PATH.write_text(json.dumps(data, indent=2))

    def corners_mm(self, marker_id: int) -> np.ndarray:
        """The 4 corners of a marker in table mm, in ArUco order (TL, TR, BR, BL as printed).

        Printed markers lie flat with their top edge pointing toward +y (away from you).
        """
        cx, cy = self.markers[marker_id]
        h = self.marker_size_mm / 2
        return np.array([[cx - h, cy + h], [cx + h, cy + h], [cx + h, cy - h], [cx - h, cy - h]], np.float32)


@dataclass
class SceneSample:
    t: float
    marker_px: dict[int, np.ndarray]  # id -> 4x2 corner pixels of every known marker seen
    H: np.ndarray | None  # 3x3, camera pixels -> table mm; None when not enough markers
    reproj_mm: float  # RMS corner error of the fit, mm (nan when no fit)
    valid: bool


class TableTracker:
    def __init__(self, config: TableConfig | None = None):
        self.config = config or TableConfig()
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX  # steadier corners on small markers
        self._detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICTIONARY), params)

    def process(self, frame: np.ndarray, t: float) -> SceneSample:
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = self._detector.detectMarkers(gray)
        seen: dict[int, np.ndarray] = {}
        if ids is not None:
            for marker_corners, marker_id in zip(corners, ids.flatten()):
                if int(marker_id) in self.config.markers:
                    seen[int(marker_id)] = marker_corners.reshape(4, 2)

        if len(seen) < self.config.min_markers:
            return SceneSample(t, seen, None, float("nan"), False)

        px = np.concatenate([seen[i] for i in seen]).astype(np.float32)
        mm = np.concatenate([self.config.corners_mm(i) for i in seen])
        H, _ = cv2.findHomography(px, mm, 0)
        if H is None:
            return SceneSample(t, seen, None, float("nan"), False)
        err = np.linalg.norm(pixels_to_table(H, px) - mm, axis=1)
        rms = float(np.sqrt((err**2).mean()))
        return SceneSample(t, seen, H, rms, rms <= self.config.max_reproj_mm)


def pixels_to_table(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Nx2 camera pixels -> Nx2 table mm."""
    return cv2.perspectiveTransform(np.asarray(pts, np.float32).reshape(-1, 1, 2), H).reshape(-1, 2)


def table_to_pixels(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Nx2 table mm -> Nx2 camera pixels."""
    return cv2.perspectiveTransform(np.asarray(pts, np.float32).reshape(-1, 1, 2), np.linalg.inv(H)).reshape(-1, 2)
