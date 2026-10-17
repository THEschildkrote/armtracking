"""Dark-pupil detector for the XIAO / OpenIris IR eye camera.

Pipeline per frame:
  1. greyscale, crop to the eye ROI, blur
  2. sweep dark thresholds; at each, clean the mask (open removes lashes,
     close fills the LED glint hole) and collect the blobs
  3. score each blob: round, pupil-sized, and much darker than the ring
     around it (a shadow or the dark frame edge fails these)
  4. keep the best blob (near the last pupil when tracking), fit an ellipse
  5. openness = pupil area / running median of recent open-eye area

Everything is in pixel coordinates of the full camera frame.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

_OPEN = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
_CLOSE = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
_RING = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))


@dataclass
class PupilSample:
    t: float  # seconds, time.monotonic()
    x: float  # pupil centre, frame pixels (nan when not valid)
    y: float
    major: float  # ellipse axes in pixels
    minor: float
    angle: float  # ellipse angle in degrees
    openness: float  # 1.0 = typical open eye, ~0 = closed
    valid: bool
    darkness: float = math.nan  # mean grey level inside the blob (pupil ~ very dark; lid crease lighter)
    contrast: float = math.nan  # ring around the blob minus inside


@dataclass
class DetectorConfig:
    roi: tuple[int, int, int, int] | None = None  # x, y, w, h; None = whole frame
    blur: int = 5
    threshold_step: int = 6  # grey levels between thresholds in the sweep
    threshold_span: int = 120  # sweep from the darkest pixel up to darkest + span
    min_area: int = 30  # pixels; tune to your pupil size at your camera distance
    max_area: int = 6000
    min_circularity: float = 0.45  # 1.0 = perfect circle; low enough for side-on pupils at extreme gaze
    min_contrast: int = 25  # ring around the blob must be this much brighter than the blob
    border: int = 4  # blobs touching this margin are the frame edge, not a pupil
    max_jump: float = 40.0  # px; when tracking, ignore candidates further than this
    area_window: int = 90  # samples in the open-eye area median (~3 s at 30 Hz)
    forget_after: int = 10  # missed frames before the last pupil position stops steering the choice


class PupilDetector:
    def __init__(self, config: DetectorConfig | None = None):
        self.config = config or DetectorConfig()
        self._last_xy: tuple[float, float] | None = None
        self._areas: deque[float] = deque(maxlen=self.config.area_window)
        self._misses = 0
        self.debug_mask: np.ndarray | None = None  # mask the pupil came from, for the viewer

    def process(self, frame: np.ndarray, t: float) -> PupilSample:
        c = self.config
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        ox, oy = 0, 0
        if c.roi:
            ox, oy, w, h = c.roi
            gray = gray[oy : oy + h, ox : ox + w]
        if gray.size == 0:
            return self._invalid(t)
        if c.blur > 1:
            gray = cv2.GaussianBlur(gray, (c.blur | 1, c.blur | 1), 0)
        h, w = gray.shape

        darkest = int(gray.min())
        best, best_score, best_mask = None, -math.inf, None
        for level in range(darkest + c.threshold_step, darkest + c.threshold_span, c.threshold_step):
            mask = (gray <= level).astype(np.uint8) * 255
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _OPEN)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _CLOSE)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            for contour in contours:
                candidate = self._score(gray, contour, w, h, ox, oy)
                if candidate and candidate[0] > best_score:
                    best_score, best, best_mask = candidate[0], candidate[1:], mask
        self.debug_mask = best_mask if best_mask is not None else np.zeros_like(gray)

        if best is None:
            return self._invalid(t)

        x, y, major, minor, angle, area, inside, contrast = best
        self._last_xy = (x, y)
        self._misses = 0
        self._areas.append(area)
        openness = area / float(np.median(self._areas))
        return PupilSample(t, x, y, major, minor, angle, openness, True, inside, contrast)

    def _score(self, gray, contour, w, h, ox, oy):
        c = self.config
        hull = cv2.convexHull(contour)  # hull ignores the notch a glint or lash cuts into the edge
        area = cv2.contourArea(hull)
        if not c.min_area <= area <= c.max_area or len(hull) < 5:
            return None
        bx, by, bw, bh = cv2.boundingRect(hull)
        if bx < c.border or by < c.border or bx + bw > w - c.border or by + bh > h - c.border:
            return None  # touches the frame edge: dark corner or shadow, not a pupil
        perimeter = cv2.arcLength(hull, True)
        circularity = 4 * math.pi * area / (perimeter * perimeter) if perimeter else 0
        if circularity < c.min_circularity:
            return None

        # Contrast: mean of a thin ring just outside the blob minus the blob's own mean.
        blob = np.zeros((h, w), np.uint8)
        cv2.drawContours(blob, [hull], -1, 255, -1)
        ring = cv2.dilate(blob, _RING) & ~blob
        inside, outside = cv2.mean(gray, blob)[0], cv2.mean(gray, ring)[0]
        contrast = outside - inside
        if contrast < c.min_contrast:
            return None

        (cx, cy), (a1, a2), angle = cv2.fitEllipse(hull)
        x, y = cx + ox, cy + oy
        score = contrast * circularity
        if self._last_xy:
            jump = math.dist((x, y), self._last_xy)
            if jump > c.max_jump:
                return None
            score -= jump  # same pupil as last frame wins ties
        return score, x, y, max(a1, a2), min(a1, a2), angle, area, inside, contrast

    def _invalid(self, t: float) -> PupilSample:
        self._misses += 1
        if self._misses >= self.config.forget_after:
            self._last_xy = None
        return PupilSample(t, math.nan, math.nan, 0.0, 0.0, 0.0, 0.0, False)

    def reset(self) -> None:
        self._last_xy = None
        self._misses = 0
        self._areas.clear()
