"""Can the arm pick this? The same plan a scripted pick would run, solved without moving anything.

can_pick(obj)   -> True/False for an object entry from gaze/objects.json (its spot, angle, grip height)
ReachMap        -> a coarse map over the board: share of grip angles that work in each cell,
                   cached in logs/reach_map.json until the arm's table calibration changes
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import numpy as np

from arm import scripted as s

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "logs" / "reach_map.json"
MAP_ANGLES = (0, 60, 120)  # jaw headings tried per cell (table frame); the jaws repeat every 180


class Reach:
    def __init__(self):
        self.table = json.loads(s.TABLE_CALIB.read_text())
        self.kin = s.Kinematics()
        self.calib_id = hashlib.md5(s.TABLE_CALIB.read_bytes()).hexdigest()[:12]
        self._lock = threading.Lock()  # one ikpy chain: one solve at a time

    def can_pick(self, obj: dict) -> bool:
        with self._lock:
            route = s.plan(self.kin, self.table, obj)
            seed = {k: 0.0 for k in s.ARM_JOINTS}
            waypoints = ([(route["high"], None)] if route["high"] is not None else []) + \
                [(route["above"], route["yaw"]), (route["at"], route["yaw"])]
            try:
                for xyz, yaw in waypoints:  # chained the way the real move solves them
                    seed = self.kin.ik(xyz, seed)
                    if yaw is not None:
                        seed = self.kin.with_yaw(seed, yaw, xyz)
                return True
            except (RuntimeError, ValueError):
                return False


class ReachMap:
    """cells[(i, j)] = share of MAP_ANGLES that work at table (x0 + i*step, y0 + j*step); filled in a thread."""

    def __init__(self, reach: Reach, region: np.ndarray, step: float = 50.0, grasp_mm: float = 15):
        self.reach, self.step, self.grasp_mm = reach, step, grasp_mm
        self.x0, self.y0 = float(region[:, 0].min()) + step / 2, float(region[:, 1].min()) + step / 2
        self.nx = int((region[:, 0].max() - region[:, 0].min()) // step)
        self.ny = int((region[:, 1].max() - region[:, 1].min()) // step)
        self.cells: dict[tuple[int, int], float] = {}
        key = f"{reach.calib_id}-{step}-{grasp_mm}-{self.x0:.0f}-{self.y0:.0f}-{self.nx}x{self.ny}"
        if MAP_PATH.exists():
            data = json.loads(MAP_PATH.read_text())
            if data.get("key") == key:
                self.cells = {tuple(map(int, k.split(","))): v for k, v in data["cells"].items()}
        self.key = key
        self.done = len(self.cells) == self.nx * self.ny
        if not self.done:
            threading.Thread(target=self._fill, daemon=True).start()

    def centre(self, i: int, j: int) -> tuple[float, float]:
        return self.x0 + i * self.step, self.y0 + j * self.step

    def _fill(self) -> None:
        # middle of the board first: that's where things usually sit
        order = sorted(((i, j) for i in range(self.nx) for j in range(self.ny)),
                       key=lambda c: abs(c[0] - self.nx / 2) + abs(c[1] - self.ny / 2))
        for n, (i, j) in enumerate(order):
            if (i, j) in self.cells:
                continue
            x, y = self.centre(i, j)
            ok = [self.reach.can_pick({"x": x, "y": y, "yaw": a, "grasp_mm": self.grasp_mm}) for a in MAP_ANGLES]
            self.cells[(i, j)] = sum(ok) / len(ok)
            if n % 10 == 9:
                self._save()
        self.done = True
        self._save()

    def _save(self) -> None:
        MAP_PATH.parent.mkdir(exist_ok=True)
        MAP_PATH.write_text(json.dumps({"key": self.key, "cells": {f"{i},{j}": v for (i, j), v in self.cells.items()}}))
