"""Step 7: decide which object the user means.

Objects sit on marked spots on the board; their table-mm centres live in
gaze/objects.json:
    {"objects": [{"name": "red", "x": 120, "y": 150}, {"name": "blue", "x": 260, "y": 150}]}

Flow:
  fixation near an object      -> "candidate"  (highlight it on screen)
  long blink looking at it     -> "selected"   (this is the command for the arm)
  double blink                 -> "cancelled"
  two objects about equally near -> "ambiguous" (nothing selected; look again)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

OBJECTS_PATH = Path(__file__).with_name("objects.json")


@dataclass
class TableObject:
    name: str
    x: float  # table mm
    y: float


@dataclass
class SelectorConfig:
    radius: float = 35.0  # mm: gaze must land this close to an object's centre
    ambiguity_margin: float = 15.0  # mm: two objects whose distances differ by less than this are ambiguous


@dataclass
class SelectionEvent:
    t: float
    kind: str  # "candidate" | "selected" | "ambiguous" | "cancelled" | "none"
    name: str | None = None
    near: list[str] = field(default_factory=list)  # objects within radius (for "ambiguous")
    x: float = math.nan  # gaze point that produced it, table mm
    y: float = math.nan


def load_objects(path: Path = OBJECTS_PATH) -> list[TableObject]:
    if not path.exists():
        return []
    return [TableObject(o["name"], float(o["x"]), float(o["y"])) for o in json.loads(path.read_text())["objects"]]


class Selector:
    def __init__(self, objects: list[TableObject], config: SelectorConfig | None = None):
        self.objects = objects
        self.config = config or SelectorConfig()
        self.candidate: str | None = None

    def resolve(self, x: float, y: float) -> tuple[str, str | None, list[str]]:
        """('one', name, near) | ('ambiguous', None, near) | ('none', None, [])."""
        dists = sorted((math.dist((x, y), (o.x, o.y)), o.name) for o in self.objects)
        near = [(d, n) for d, n in dists if d <= self.config.radius]
        if not near:
            return "none", None, []
        if len(near) > 1 and near[1][0] - near[0][0] < self.config.ambiguity_margin:
            return "ambiguous", None, [n for _, n in near]
        return "one", near[0][1], [n for _, n in near]

    def on_fixation(self, t: float, x: float, y: float) -> SelectionEvent:
        kind, name, near = self.resolve(x, y)
        if kind == "one":
            self.candidate = name
            return SelectionEvent(t, "candidate", name, near, x, y)
        self.candidate = None
        return SelectionEvent(t, "ambiguous" if kind == "ambiguous" else "none", None, near, x, y)

    def on_blink(self, t: float, blink_kind: str, x: float, y: float) -> SelectionEvent | None:
        """x, y: table mm of where the user looked just before the blink (nan if unknown)."""
        if blink_kind == "double":
            self.candidate = None
            return SelectionEvent(t, "cancelled", None, [], x, y)
        if blink_kind != "long":
            return None
        if not math.isnan(x) and not math.isnan(y):
            kind, name, near = self.resolve(x, y)
            if kind == "ambiguous":
                return SelectionEvent(t, "ambiguous", None, near, x, y)
            if kind == "one":
                self.candidate = None
                return SelectionEvent(t, "selected", name, near, x, y)
        if self.candidate:  # gaze point unknown (e.g. table out of view): fall back to the highlighted object
            name, self.candidate = self.candidate, None
            return SelectionEvent(t, "selected", name, [name], x, y)
        return SelectionEvent(t, "none", None, [], x, y)
