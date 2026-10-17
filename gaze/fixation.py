"""Step 6: turn noisy gaze points into fixations (deliberate looks at one spot).

Per gaze point (table mm, or front-camera px when the table is not in view):
  1. median of the last 5 valid points   - kills single-frame spikes
  2. exponential smoothing (alpha)        - steadies the dot
  3. saccade / blink rejection            - fast jumps or a gap reset the dwell
  4. dwell: stays within radius of where it settled for dwell_s -> one Fixation
     event, then no more until the gaze leaves that circle
`progress` (0..1) drives the dashboard's filling ring.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass
class FixationConfig:
    median_n: int = 5
    alpha: float = 0.3  # EMA weight of the newest point; lower = smoother but laggier
    radius: float = 40.0  # mm (or px in front-camera units)
    dwell_s: float = 0.8
    max_speed: float = 300.0  # mm/s; faster = saccade
    max_gap_s: float = 0.15  # longer without valid gaze (blink, lost) resets the dwell


@dataclass
class Fixation:
    t: float  # when the dwell completed
    x: float
    y: float
    duration: float  # dwell time at the moment of firing (= dwell_s)


class FixationDetector:
    def __init__(self, config: FixationConfig | None = None):
        self.config = config or FixationConfig()
        self._window: deque[tuple[float, float]] = deque(maxlen=self.config.median_n)
        self._smooth: tuple[float, float] | None = None
        self._last_t: float | None = None
        self._anchor: tuple[float, float] | None = None
        self._anchor_t = 0.0
        self._fired = False
        self.progress = 0.0  # 0..1 toward the next fixation
        self.position: tuple[float, float] | None = None  # filtered gaze

    def update(self, t: float, x: float, y: float, valid: bool) -> Fixation | None:
        c = self.config
        if not valid or math.isnan(x) or math.isnan(y):
            if self._last_t is not None and t - self._last_t > c.max_gap_s:
                self._reset()
            return None

        if self._last_t is not None and t - self._last_t > c.max_gap_s:
            self._reset()  # came back after a blink / dropout: start fresh
        self._window.append((x, y))
        mx = sorted(p[0] for p in self._window)[len(self._window) // 2]
        my = sorted(p[1] for p in self._window)[len(self._window) // 2]
        prev = self._smooth
        if prev is None:
            self._smooth = (mx, my)
        else:
            self._smooth = (prev[0] + c.alpha * (mx - prev[0]), prev[1] + c.alpha * (my - prev[1]))
        dt = t - self._last_t if self._last_t is not None else 0.0
        self._last_t = t
        self.position = self._smooth

        speed = math.dist(self._smooth, prev) / dt if prev is not None and dt > 0 else 0.0
        if speed > c.max_speed:
            self._anchor, self._fired, self.progress = None, False, 0.0  # saccade: restart the dwell
            return None

        if self._anchor is None or math.dist(self._smooth, self._anchor) > c.radius:
            self._anchor, self._anchor_t, self._fired = self._smooth, t, False  # settled somewhere new
            self.progress = 0.0
            return None

        held = t - self._anchor_t
        self.progress = min(held / c.dwell_s, 1.0)
        if held >= c.dwell_s and not self._fired:
            self._fired = True
            return Fixation(t, self._smooth[0], self._smooth[1], held)
        return None

    def _reset(self) -> None:
        self._window.clear()
        self._smooth = None
        self._anchor, self._fired, self.progress = None, False, 0.0
        self.position = None
