"""Step 2: which pupil samples are usable for gaze, and blink commands.

Blinks are the command channel, so they are classified, not just thrown away:
  "blink"   a short natural blink - reported for logging, not meant as a command
  "long"    eye held closed >= long_blink_s       (e.g. "confirm / pick this")
  "double"  two short blinks within double_gap_s  (e.g. "cancel")
Every event carries gaze_before: the pupil position from just before the lid
started to move, i.e. where the user was looking when they decided to blink.

What counts as a blink (tuned on a recording of extreme glances):
  - only the pupil VANISHING closes the eye. A pupil that merely looks small or
    thin is usually the camera seeing it side-on at an extreme gaze, so it is
    marked untrusted for gaze but is not a blink.
  - it must vanish SUDDENLY: the pupil looked normal 60-250 ms before. Losing it
    gradually (getting thinner as you look further away) is tracking loss.
  - "long" and "double" must reopen looking at the same spot they closed on,
    so glancing away and back is never a command.

usable=False for: no pupil, small/thin pupil, the first settle_s after reopening,
and single-frame jumps that the next frame does not confirm.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from eye.detector import PupilSample


@dataclass
class ValidityConfig:
    partial_openness: float = 0.35  # area below this fraction of recent normal -> not trusted for gaze
    flat_ratio: float = 0.25  # ellipse minor/major below this -> not trusted for gaze
    normal_openness: float = 0.6  # what a "normal" pupil looks like, for the sudden-vanish check
    normal_ratio: float = 0.4
    sudden_from_s: float = 0.25  # the pupil must have looked normal from this long before it vanished...
    sudden_to_s: float = 0.06  # ...until this long before (the lid itself takes a few frames)
    min_blink_s: float = 0.05  # shorter dropouts are detector glitches, not blinks
    long_blink_s: float = 0.8  # natural blinks are ~0.1-0.4 s, but some reached ~0.48 s in testing
    max_blink_s: float = 2.0  # gone longer than this = eyes shut / tracking lost, no command
    double_gap_s: float = 0.5  # second short blink must start this soon after the first reopens
    same_spot_px: float = 30.0  # command blinks must reopen within this distance of where they closed
    settle_s: float = 0.10  # samples right after reopening are unreliable
    pre_blink_s: float = 0.15  # gaze_before is taken this long before the pupil vanished
    max_jump_px: float = 15.0  # bigger single-frame jumps need the next frame to confirm them
    # A blob this many grey levels lighter than your usual pupil is the lid crease / lash shadow the
    # detector finds when the eye is shut, not the pupil -> counts as closed.
    dark_margin: float = 25.0
    dark_history: int = 300  # usable samples in the "usual pupil darkness" median (~5 s at 60 Hz)


@dataclass
class BlinkEvent:
    t: float  # when it was decided (reopen time; for "blink" up to double_gap_s later)
    kind: str  # "blink" | "long" | "double"
    duration: float  # closed time in seconds (for "double": the second blink)
    gaze_before: tuple[float, float] | None  # pupil (x, y) before the (first) blink, frame pixels


@dataclass
class EyeState:
    sample: PupilSample
    usable: bool  # safe to use for gaze
    closed: bool  # pupil gone (blink, eyes shut, or tracking lost)
    reason: str = ""  # why not usable: "no pupil" | "small" | "flat" | "settling" | "jump" ("" when usable)


class EyeValidity:
    def __init__(self, config: ValidityConfig | None = None):
        self.config = config or ValidityConfig()
        self._history: deque[tuple[float, float, float]] = deque(maxlen=240)  # (t, x, y) of usable samples
        self._recent: deque[tuple[float, bool]] = deque(maxlen=60)  # (t, looked normal) of every sample
        self._darkness: deque[float] = deque(maxlen=self.config.dark_history)  # inside grey of usable pupils
        self._closed = False
        self._closed_at = 0.0
        self._sudden = False
        self._gaze_at_close: tuple[float, float] | None = None
        self._settle_until = 0.0
        self._last_xy: tuple[float, float] | None = None
        self._jump_candidate: tuple[float, float] | None = None
        self._pending_blink: BlinkEvent | None = None  # first short blink, waiting to see if a second follows
        self._pending_reopen = 0.0

    def update(self, s: PupilSample) -> tuple[EyeState, list[BlinkEvent]]:
        c = self.config
        events: list[BlinkEvent] = []
        ratio = s.minor / s.major if s.valid and s.major > 0 else 0.0
        not_pupil = (
            s.valid
            and len(self._darkness) >= 30
            and not math.isnan(s.darkness)
            and s.darkness > self.usual_darkness() + c.dark_margin
        )
        present = s.valid and not not_pupil

        if not self._closed and not present:
            self._closed, self._closed_at = True, s.t
            self._sudden = self._looked_normal_before(s.t)
            self._gaze_at_close = self._gaze_before(s.t)
        elif self._closed and present:
            self._closed = False
            self._settle_until = s.t + c.settle_s
            self._last_xy, self._jump_candidate = None, None
            if self._sudden:
                events += self._on_reopen(s.t, s.t - self._closed_at, (s.x, s.y))
        self._recent.append((s.t, present and s.openness >= c.normal_openness and ratio >= c.normal_ratio))

        # A lone short blink becomes a plain "blink" once no second one arrived in time.
        if self._pending_blink and not self._closed and s.t - self._pending_reopen > c.double_gap_s:
            events.append(BlinkEvent(s.t, "blink", self._pending_blink.duration, self._pending_blink.gaze_before))
            self._pending_blink = None

        if not s.valid:
            reason = "no pupil"
        elif not_pupil:
            reason = "not pupil"
        elif s.openness < c.partial_openness:
            reason = "small"
        elif ratio < c.flat_ratio:
            reason = "flat"
        elif s.t < self._settle_until:
            reason = "settling"
        elif not self._accept_position(s):
            reason = "jump"
        else:
            reason = ""
        if not reason:
            self._history.append((s.t, s.x, s.y))
            if not math.isnan(s.darkness):
                self._darkness.append(s.darkness)
        return EyeState(s, not reason, self._closed, reason), events

    def usual_darkness(self) -> float:
        """Median inside-grey of recent trusted pupils (nan until enough samples)."""
        if not self._darkness:
            return math.nan
        values = sorted(self._darkness)
        return values[len(values) // 2]

    def closed_for(self, t: float) -> float:
        """Seconds the pupil has been gone at time t (0 when visible)."""
        return t - self._closed_at if self._closed else 0.0

    def _looked_normal_before(self, t_close: float) -> bool:
        c = self.config
        window = [normal for t, normal in self._recent if t_close - c.sudden_from_s <= t <= t_close - c.sudden_to_s]
        return len(window) >= 3 and sum(window) >= 0.8 * len(window)

    def _on_reopen(self, t: float, duration: float, xy_after: tuple[float, float]) -> list[BlinkEvent]:
        c = self.config
        if duration < c.min_blink_s or duration > c.max_blink_s:
            return []
        same_spot = self._gaze_at_close is not None and math.dist(xy_after, self._gaze_at_close) <= c.same_spot_px
        if duration >= c.long_blink_s:
            self._pending_blink = None
            return [BlinkEvent(t, "long", duration, self._gaze_at_close)] if same_spot else []
        if self._pending_blink and self._closed_at - self._pending_reopen <= c.double_gap_s:
            first = self._pending_blink
            self._pending_blink = None
            if first.gaze_before and math.dist(xy_after, first.gaze_before) <= c.same_spot_px:
                return [BlinkEvent(t, "double", duration, first.gaze_before)]
            return []
        self._pending_blink = BlinkEvent(t, "blink", duration, self._gaze_at_close)
        self._pending_reopen = t
        return []

    def _accept_position(self, s: PupilSample) -> bool:
        """Reject one-frame jumps; a real eye movement shows up again on the next frame."""
        xy = (s.x, s.y)
        if self._last_xy is None or math.dist(xy, self._last_xy) <= self.config.max_jump_px:
            self._last_xy, self._jump_candidate = xy, None
            return True
        if self._jump_candidate and math.dist(xy, self._jump_candidate) <= self.config.max_jump_px / 2:
            self._last_xy, self._jump_candidate = xy, None  # confirmed: the eye really moved
            return True
        self._jump_candidate = xy
        return False

    def _gaze_before(self, t_close: float) -> tuple[float, float] | None:
        target = t_close - self.config.pre_blink_s
        for t, x, y in reversed(self._history):
            if t <= target:
                return (x, y)
        return (self._history[0][1], self._history[0][2]) if self._history else None
