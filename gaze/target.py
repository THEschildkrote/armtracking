"""Find an on-screen calibration target in a low-res, blurry front-camera frame.

The target is a black disc on a white full-screen window. ArUco markers are too
small to decode at 240x240 (a few pixels per cell), but a dark blob inside the
bright screen is easy: find the screen (largest bright, roughly rectangular
region), then the largest dark, roundish blob inside it, and take its centroid.
"""

from __future__ import annotations

import cv2
import numpy as np


def find_screen(gray: np.ndarray) -> np.ndarray | None:
    """Mask of the laptop screen: the largest bright, roughly rectangular region."""
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best, best_area = None, 0.02 * gray.size
    for c in contours:
        area = cv2.contourArea(cv2.convexHull(c))
        (_, (w, h), _) = cv2.minAreaRect(c)
        if area > best_area and w * h > 0 and area / (w * h) > 0.7:
            best, best_area = c, area
    if best is None:
        return None
    mask = np.zeros_like(gray)
    cv2.drawContours(mask, [cv2.convexHull(best)], -1, 255, -1)  # hull: the dark target makes a hole in the screen
    return mask


def find_target(frame: np.ndarray, min_contrast: int = 18) -> tuple[float, float] | None:
    """Centre (u, v) of the dark disc on the screen, in frame pixels, or None."""
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    screen = find_screen(gray)
    if screen is None:
        return None
    inner = cv2.erode(screen, np.ones((3, 3), np.uint8))  # ignore the dark bezel right at the screen edge
    if not np.any(inner):
        return None
    # Screen glow washes the black target out to mid-grey and the screen is unevenly bright, so compare
    # each pixel with its local surroundings: closing fills the dark target in with nearby screen brightness.
    background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))
    contrast = cv2.subtract(background, gray)
    dark = ((contrast > min_contrast) & (inner > 0)).astype(np.uint8) * 255
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    best, best_area = None, 12.0
    for c in contours:
        area = cv2.contourArea(c)
        perimeter = cv2.arcLength(c, True)
        roundness = 4 * np.pi * area / (perimeter * perimeter) if perimeter else 0
        if area > best_area and roundness > 0.4:
            best, best_area = c, area
    if best is None:
        return None
    m = cv2.moments(best)
    return m["m10"] / m["m00"], m["m01"] / m["m00"]


def target_disc(size: int) -> np.ndarray:
    """A black disc with a small red fixation dot, on white."""
    img = np.full((size, size, 3), 255, np.uint8)
    c = size // 2
    cv2.circle(img, (c, c), int(size * 0.45), (0, 0, 0), -1)
    cv2.circle(img, (c, c), max(3, size // 40), (0, 0, 255), -1)
    return img
