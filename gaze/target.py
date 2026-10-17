"""Find a calibration target - a black disc on a bright screen - in a low-res, blurry front-camera frame.

The screen can be the laptop (screen calibration) or a phone lying on the table
(table calibration). ArUco markers are too small to decode at 240x240 (a few
pixels per cell), but a dark blob inside a bright screen is easy: find the bright,
roughly rectangular regions, then the largest dark, roundish blob inside one of
them, and take its centroid.
"""

from __future__ import annotations

import cv2
import numpy as np


def find_screens(gray: np.ndarray, max_screens: int = 3) -> list[np.ndarray]:
    """Masks of bright, roughly rectangular regions (laptop / phone screens), largest first."""
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    scene_level = float(np.median(gray))
    found = []
    for c in contours:
        hull = cv2.convexHull(c)  # hull: the dark target makes a hole in the screen
        area = cv2.contourArea(hull)
        (_, (w, h), _) = cv2.minAreaRect(c)
        if not (area > 0.004 * gray.size and w * h > 0 and area / (w * h) > 0.7):
            continue
        mask = np.zeros_like(gray)
        cv2.drawContours(mask, [hull], -1, 255, -1)
        # a lit screen is much brighter than the room around it; a glare patch on the desk is not
        if np.percentile(gray[mask > 0], 75) < scene_level + 40:
            continue
        found.append((area, hull))
    masks = []
    for _, hull in sorted(found, key=lambda f: -f[0])[:max_screens]:
        mask = np.zeros_like(gray)
        cv2.drawContours(mask, [hull], -1, 255, -1)
        masks.append(mask)
    return masks


def find_screen(gray: np.ndarray) -> np.ndarray | None:
    """Mask of the largest screen-like region (kept for callers that want just one)."""
    screens = find_screens(gray, 1)
    return screens[0] if screens else None


def _phone_like(screen: np.ndarray, disc: np.ndarray) -> bool:
    """The phone target's layout: a tall bright rectangle (~2.2:1) with the disc centred in it, covering
    roughly a quarter of it. A laptop screen with dark UI on it, or a ceiling light, doesn't look like that."""
    contours, _ = cv2.findContours(screen, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False
    (sx, sy), (w, h), _ = cv2.minAreaRect(max(contours, key=cv2.contourArea))
    long_side, short_side = max(w, h), min(w, h)
    if short_side <= 0 or not 1.4 <= long_side / short_side <= 3.2:
        return False
    m = cv2.moments(disc)
    if not m["m00"]:
        return False
    if np.hypot(m["m10"] / m["m00"] - sx, m["m01"] / m["m00"] - sy) > 0.2 * long_side:
        return False
    # the disc spans ~80% of the phone's width; dark things on a laptop screen are small next to its width
    (_, _), (dw, dh), _ = cv2.minAreaRect(disc)
    if not 0.5 <= max(dw, dh) / short_side <= 1.05:
        return False
    return 0.08 <= cv2.contourArea(disc) / float((screen > 0).sum()) <= 0.42


def find_target(frame: np.ndarray, min_contrast: int = 18, phone: bool = False) -> tuple[float, float] | None:
    """Centre (u, v) of the dark disc on a screen, in frame pixels, or None.
    phone=True (table calibration) accepts only the phone target's layout - see _phone_like."""
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    best, best_area = None, 12.0
    for screen in find_screens(gray):
        screen_area = float((screen > 0).sum())
        # Screen glow washes the black target out to mid-grey and screens are unevenly bright, so compare
        # each pixel with its local surroundings: closing fills the dark target in with nearby screen
        # brightness. The kernel must be wider than the disc, so it scales with the screen's size.
        k = max(21, int(0.8 * np.sqrt(screen_area)) | 1)
        background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        contrast = cv2.subtract(background, gray)
        inner = cv2.erode(screen, np.ones((3, 3), np.uint8))  # ignore the dark bezel right at the screen edge
        dark = ((contrast > min_contrast) & (inner > 0)).astype(np.uint8) * 255
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        for c in contours:
            area = cv2.contourArea(c)
            perimeter = cv2.arcLength(c, True)
            roundness = 4 * np.pi * area / (perimeter * perimeter) if perimeter else 0
            if area <= best_area or roundness <= 0.4 or not 0.03 <= area / screen_area <= 0.45:
                continue
            blob = np.zeros_like(gray)
            cv2.drawContours(blob, [c], -1, 255, -1)
            if cv2.mean(contrast, blob)[0] < 1.4 * min_contrast:  # a real disc is solidly darker than the screen
                continue
            if phone and not _phone_like(screen, c):
                continue
            best, best_area = c, area
    if best is None:
        return None
    m = cv2.moments(best)
    return m["m10"] / m["m00"], m["m01"] / m["m00"]


def target_disc(size: int, big_dot: bool = False) -> np.ndarray:
    """A black disc with a red fixation dot, on white.

    The on-screen targets keep the original tiny dot: a bright ring inside the disc breaks it up in Board B's
    blurry 240x240 view and the finder stops seeing it. The phone target (big_dot) gets a bigger, ringed dot."""
    img = np.full((size, size, 3), 255, np.uint8)
    c = size // 2
    cv2.circle(img, (c, c), int(size * 0.45), (0, 0, 0), -1)
    if big_dot:
        dot = max(3, size // 12)
        cv2.circle(img, (c, c), dot + max(2, size // 90), (255, 255, 255), -1)
        cv2.circle(img, (c, c), dot, (0, 0, 255), -1)
    else:
        cv2.circle(img, (c, c), max(3, size // 40), (0, 0, 255), -1)
    return img


def phone_target(width: int = 1080, height: int = 2340) -> np.ndarray:
    """Full-screen phone image for table calibration: white screen, big black disc, red dot."""
    img = np.full((height, width, 3), 255, np.uint8)
    disc = target_disc(int(width * 0.8), big_dot=True)
    y0, x0 = (height - disc.shape[0]) // 2, (width - disc.shape[1]) // 2
    img[y0 : y0 + disc.shape[0], x0 : x0 + disc.shape[1]] = disc
    return img
