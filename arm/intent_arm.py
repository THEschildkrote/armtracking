"""Real arm behind the intent Shell (intent/look_intent): request_prehover(id), cancel_prehover(), grasp(id).

The Shell decides; this class only moves, one command at a time on its own thread,
so the gaze loop never blocks on the arm. Safety it enforces itself (it does not
trust the caller):
  - grasps only objects in arm/policies.json, hovers only with a table calibration
  - every pose is IK-checked before anything moves (arm/scripted.py), all moves are eased
  - nothing new starts while a pick is running; a cancel never interrupts a descent
  - "arm_descend" is logged at the real descent, not when the command is accepted

Pre-hover needs the scripted IK (table -> robot). A grasp then either continues from
the hover with the scripted pick, or - for an ACT policy - glides home first, because
policies were trained starting from the home pose.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Callable

from arm.motion import go_home
from arm.runner import POLICIES_PATH, ArmRunner


class IntentArm:
    def __init__(self, port: str = "COM4", log: Callable[[dict], None] | None = None,
                 on_grasp_ok: Callable[[str, float, bool], None] | None = None):
        self.port = port
        self.log = log or (lambda e: None)
        self.on_grasp_ok = on_grasp_ok  # (object_id, t, verified) -> Shell.grasp_ok
        self.status = "idle"
        self.hover_id: str | None = None  # where the arm is hovering now
        self._want: str | None = None  # the hover the Shell currently wants
        self._busy = False  # a grasp is running
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    # ---- the three calls the Shell makes (never block) ----
    def request_prehover(self, object_id: str) -> None:
        if not self._busy and self._want != object_id:
            self._want = object_id
            self._q.put(("hover", object_id))

    def cancel_prehover(self) -> None:
        if not self._busy and (self._want is not None or self.hover_id is not None):
            self._want = None
            self._q.put(("home", None))

    def grasp(self, object_id: str) -> None:
        self._want = object_id
        self._q.put(("grasp", object_id))

    # ---- worker ----
    def _next(self):
        cmd = self._q.get()
        while True:  # coalesce: a waiting grasp wins, otherwise only the newest hover/home matters
            try:
                nxt = self._q.get_nowait()
            except queue.Empty:
                return cmd
            if cmd[0] != "grasp":
                cmd = nxt

    def _worker(self) -> None:
        while True:
            kind, object_id = self._next()
            try:
                if kind == "hover" and object_id == self._want:  # skip hovers cancelled while queued
                    self._hover(object_id)
                elif kind == "home" and self.hover_id is not None:
                    self.status = "returning home"
                    go_home(self.port, relax=False)
                    self.hover_id = None
                    self.status = "idle"
                elif kind == "grasp":
                    self._grasp(object_id)
            except Exception as e:
                self.status = f"error: {e}"
                self.log({"type": "arm_error", "t": time.monotonic(), "action": kind,
                          "object_id": object_id, "error": str(e)[:200]})
                self.hover_id, self._busy = None, False

    def _spec(self, object_id: str) -> dict:
        specs = json.loads(POLICIES_PATH.read_text())
        if object_id not in specs:
            raise RuntimeError(f"{object_id!r} is not in arm/policies.json")
        return specs[object_id]

    def _hover(self, object_id: str) -> None:
        from arm.scripted import TABLE_CALIB, hover

        if not TABLE_CALIB.exists():
            self.status = "pre-hover off (no table calibration)"
            return
        self._spec(object_id)  # unknown objects are never approached
        self.status = f"pre-hover {object_id}"
        hover(object_id, self.port)
        self.hover_id = object_id
        self.log({"type": "prehover_reached", "t": time.monotonic(), "object_id": object_id})

    def _grasp(self, object_id: str) -> None:
        spec = self._spec(object_id)
        self._busy = True
        try:
            if spec["type"] == "scripted":
                from arm.scripted import hover, pick_from_hover

                if self.hover_id != object_id:
                    self.status = f"moving to {object_id}"
                    hover(object_id, self.port)
                self.status = f"picking {object_id}"
                held = pick_from_hover(object_id, self.port, on_descend=lambda: self._descend(object_id))
                self._grasped(object_id, verified=True, held=held)
            elif spec["type"] == "policy":
                self.status = "homing for policy"
                go_home(self.port, relax=False)  # policies start from home
                self.status = f"policy {object_id}"
                self._descend(object_id)  # the policy descends on its own soon after starting
                ArmRunner(self.port)._rollout(spec)
                self._grasped(object_id, verified=False, held=True)
            else:
                raise RuntimeError(f"{spec['type']!r} actions can't grasp")
            self.status = "returning home"
            go_home(self.port, relax=True)
            self.log({"type": "released", "t": time.monotonic(), "object_id": object_id})
            self.status = "idle"
        finally:
            self.hover_id, self._want, self._busy = None, None, False

    def _descend(self, object_id: str) -> None:
        self.log({"type": "arm_descend", "t": time.monotonic(), "object_id": object_id})

    def _grasped(self, object_id: str, verified: bool, held: bool) -> None:
        t = time.monotonic()
        if held and self.on_grasp_ok:
            self.on_grasp_ok(object_id, t, verified)
        elif not held:
            self.log({"type": "grasp_failed", "t": t, "object_id": object_id})
        self.log({"type": "delivered", "t": t, "object_id": object_id, "held": held})
