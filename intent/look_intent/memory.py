"""Task memory: where each object came from, and what was done last.

Backs the voice commands "put it back" and "do that again". Persists to a JSON
file so history survives restarts and feeds the intent prior across sessions.
Geometry checks run LOCALLY, always: a remembered spot is only a suggestion until
it passes workspace, no-go and clearance checks against the CURRENT scene.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, asdict
from typing import Sequence

from .scoring import Obj


@dataclass
class Pick:
    t: float
    object_id: str
    cls: str
    origin_x: float
    origin_y: float
    delivered: bool = False


@dataclass(frozen=True)
class Workspace:
    x_min: float = -250.0
    x_max: float = 250.0
    y_min: float = 100.0
    y_max: float = 450.0
    nogo_x: float = 0.0        # centre of user no-go disc (table mm)
    nogo_y: float = 0.0
    nogo_r: float = 250.0      # PRD R6: stop >= 25 cm from face
    clearance_mm: float = 50.0


class TaskMemory:
    def __init__(self, path: str | None = None):
        self.path = path
        self.picks: list[Pick] = []
        if path and os.path.exists(path):
            with open(path) as f:
                self.picks = [Pick(**d) for d in json.load(f)]

    def save(self) -> None:
        if self.path:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump([asdict(p) for p in self.picks], f)
            os.replace(tmp, self.path)

    # --- recording -------------------------------------------------------
    def record_pick(self, t, object_id, cls, x, y) -> None:
        self.picks.append(Pick(t, object_id, cls, x, y))
        self.save()

    def record_delivered(self, object_id) -> None:
        for p in reversed(self.picks):
            if p.object_id == object_id:
                p.delivered = True
                break
        self.save()

    # --- queries ---------------------------------------------------------
    def history_classes(self, n: int = 50) -> list[str]:
        return [p.cls for p in self.picks[-n:]]

    def last_task(self) -> Pick | None:
        return self.picks[-1] if self.picks else None

    def origin_of(self, object_id: str) -> tuple[float, float] | None:
        for p in reversed(self.picks):
            if p.object_id == object_id:
                return (p.origin_x, p.origin_y)
        return None

    def put_back_target(self, object_id: str, scene: Sequence[Obj],
                        ws: Workspace = Workspace()) -> tuple[tuple[float, float] | None, str]:
        """Returns ((x, y), "ok") or (None, reason). Reason is speakable."""
        o = self.origin_of(object_id)
        if o is None:
            return None, "I don't know where that came from."
        x, y = o
        if not (ws.x_min <= x <= ws.x_max and ws.y_min <= y <= ws.y_max):
            return None, "Its old spot is outside my reach."
        if math.hypot(x - ws.nogo_x, y - ws.nogo_y) < ws.nogo_r:
            return None, "Its old spot is too close to you."
        for s in scene:
            if s.id != object_id and math.hypot(s.x - x, s.y - y) < ws.clearance_mm:
                return None, f"Something is in its old spot now: the {s.cls}."
        return (x, y), "ok"
