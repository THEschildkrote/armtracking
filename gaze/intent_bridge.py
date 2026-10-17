"""Bridge from the live gaze pipeline to the team's intent agent (intent/look_intent).

BlinkShell is look_intent.shell.Shell with one change of policy, agreed for the demo:
  dwell  -> the intent agent may PRE-HOVER (arm moves above the object, no descent)
  LONG BLINK while dwelled -> authorizes the DESCENT
Concretely, compared with Shell:
  * a completed 0.8 s dwell does not descend by itself; it makes that object confirmable
  * while the eyes are shut (the confirming blink) the pre-hover is held, not cancelled
  * blink_confirm() logs fixation_fired (method="long_blink") so the replay audit still
    finds an authorization before every arm_descend
  * arm_descend is logged by the arm (arm/intent_arm.py) at the real descent
Everything else - IntentService (Chalk or local fallback), scoring, task memory and the
JSONL event log the evals read - is the team's code, unchanged.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "intent"))
from look_intent.client import IntentService  # noqa: E402
from look_intent.memory import TaskMemory  # noqa: E402
from look_intent.scoring import Obj, Params, current_dwell  # noqa: E402
from look_intent.shell import Shell  # noqa: E402

OBJECTS_PATH = ROOT / "gaze" / "objects.json"
LOG_DIR = ROOT / "logs"
CONFIRM_DWELL_S = 0.8  # dwell needed before a long blink may authorize descent
CONFIRM_WINDOW_S = 1.5  # the blink must come this soon after the dwell completed
RADIUS_MM = 40.0


def load_scene() -> list[Obj]:
    """Objects from gaze/objects.json as intent Obj (cls defaults to the name)."""
    data = json.loads(OBJECTS_PATH.read_text())
    return [Obj(o["name"], o.get("cls", o["name"]), float(o["x"]), float(o["y"])) for o in data["objects"]]


class JsonlLog:
    """Thread-safe session log in the format intent/evals/replay.py reads."""

    def __init__(self, path: Path | None = None):
        LOG_DIR.mkdir(exist_ok=True)
        self.path = path or LOG_DIR / f"session-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        self._f = open(self.path, "a")
        self._lock = threading.Lock()

    def write(self, event: dict) -> None:
        with self._lock:
            self._f.write(json.dumps(event) + "\n")
            self._f.flush()

    def close(self) -> None:
        self._f.close()


class BlinkShell(Shell):
    def __init__(self, arm, service, log, memory_path: Path | None = None):
        super().__init__(arm, service, log, memory=TaskMemory(str(memory_path) if memory_path else None))
        self.eyes_closed = False
        self.confirmable: tuple[str, float] | None = None  # (object id, when its dwell completed)
        self.dwelling: str | None = None

    def set_eyes_closed(self, closed: bool) -> None:
        with self.lock:
            self.eyes_closed = closed

    def sample(self, t, x, y, valid=True):
        with self.lock:
            self.emit("gaze", t, x=x, y=y, valid=valid)
            self.revision += 1
            if not valid:
                if not self.eyes_closed:  # tracking lost with the eyes open: drop everything
                    self.gaze, self.fired, self.confirmable = [], None, None
                    self._dwell(None, t)
                    self.cancel(t)
                return
            self.gaze.append((t, x, y))
            self.gaze = [g for g in self.gaze if t - g[0] <= 1.5]
            candidates = sorted(self.objects, key=lambda o: (o.x - x) ** 2 + (o.y - y) ** 2)
            target = candidates[0] if candidates else None

            def d(o):
                return ((o.x - x) ** 2 + (o.y - y) ** 2) ** 0.5

            ambiguous = len(candidates) > 1 and d(candidates[1]) <= 1.5 * d(target)
            if target is None or d(target) > RADIUS_MM or ambiguous:
                self.fired = None
                self._dwell(None, t)
                self.cancel(t)
                return
            self._dwell(target.id, t)
            if self.fired != target.id:
                self.fired = None
            if current_dwell(self.gaze, target, t, Params(radius_mm=RADIUS_MM)) >= CONFIRM_DWELL_S:
                if not self.confirmable or self.confirmable[0] != target.id:
                    self.emit("dwell_complete", t, object_id=target.id)
                self.confirmable = (target.id, t)  # refreshed while the gaze stays on it

    def _dwell(self, object_id, t):
        if object_id != self.dwelling:
            if self.dwelling is not None:
                self.emit("dwell_cancel", t, object_id=self.dwelling)
            if object_id is not None:
                self.emit("dwell_start", t, object_id=object_id)
            self.dwelling = object_id

    def tick(self, t, clock=None):
        with self.lock:
            if self.eyes_closed:
                return None  # mid-blink: hold the current pre-hover, start nothing new
        return super().tick(t, clock)

    def blink_confirm(self, t: float, x: float | None = None, y: float | None = None) -> str | None:
        """Long blink: authorize descent onto the dwelled-on object. Returns its id, or None."""
        with self.lock:
            if self.confirmable is None or t - self.confirmable[1] > CONFIRM_WINDOW_S:
                self.emit("blink_ignored", t, reason="no completed dwell just before the blink")
                return None
            object_id = self.confirmable[0]
            obj = next((o for o in self.objects if o.id == object_id), None)
            if obj is None:
                self.emit("blink_ignored", t, reason="object no longer in the scene")
                return None
            if x is not None and y is not None and ((obj.x - x) ** 2 + (obj.y - y) ** 2) ** 0.5 > RADIUS_MM:
                self.emit("blink_ignored", t, reason="looked away from the object before blinking")
                return None
            if self.active_task or self.held_id:
                self.emit("blink_ignored", t, reason="a pick is still running")
                return None
            # Open a pick task the way Shell.confirm does (grasp_ok / delivered / released close it);
            # the dwell was already checked above, when it completed, not at blink time.
            self.cancel(t)
            self.revision += 1
            self.active_task = uuid.uuid4().hex
            self.emit("fixation_fired", t, object_id=object_id, method="long_blink",
                      task_id=self.active_task, operation="pick")
            self.pick_origin = obj
            self.arm.grasp(object_id)  # the arm logs arm_descend when it really descends
            self.fired, self.confirmable = object_id, None
            return object_id

    def cancel_all(self, t: float) -> None:
        """Double blink: drop the pre-hover and any pending confirmation."""
        with self.lock:
            self.confirmable, self.fired = None, None
            self.cancel(t)

    def grasp_done(self, object_id: str, t: float, verified: bool) -> None:
        """Arm callback after a successful pick: task memory, close the task (the scripted pick has
        already carried it to the drop spot and let go), and remove the object from the scene."""
        try:
            self.grasp_ok(object_id, t)
            self.emit("grasp_verified", t, object_id=object_id, verified=verified)
            self.delivered(object_id, t)
            self.released(object_id, t)
        except ValueError as e:  # bookkeeping out of step: log it, never leave a task stuck open
            self.emit("task_mismatch", t, object_id=object_id, error=str(e))
            self.pick_failed(object_id, t, reason=f"bookkeeping: {e}")
        with self.lock:
            remaining = [o for o in self.objects if o.id != object_id]
        self.scene(remaining, t)

    def pick_failed(self, object_id: str, t: float, reason: str = "") -> None:
        """Arm callback when a pick held nothing or errored: close the task so the next dwell can
        pre-hover again (an open task blocks pre-hover and new picks)."""
        with self.lock:
            self.emit("pick_failed", t, object_id=object_id, task_id=self.active_task, reason=reason)
            self.active_task = self.held_id = self.fired = None
            self.pick_origin = None
            self.gaze = []
            self.revision += 1


def make_intent(arm_port: str = "COM4", use_chalk: bool = False, user_id: str = "demo"):
    """Build log + IntentService + real arm + BlinkShell, wired together. Returns (shell, arm, log)."""
    from arm.intent_arm import IntentArm

    log = JsonlLog()
    service = IntentService(user_id, use_chalk=use_chalk, log=log.write)
    shell_box: dict = {}
    arm = IntentArm(arm_port, log=log.write,
                    on_grasp_ok=lambda oid, t, verified: shell_box["shell"].grasp_done(oid, t, verified),
                    on_grasp_failed=lambda oid, t, reason: shell_box["shell"].pick_failed(oid, t, reason))
    shell = BlinkShell(arm, service, log.write, memory_path=LOG_DIR / "task_memory.json")
    shell_box["shell"] = shell
    shell.emit("session", time.monotonic(), synthetic=False, mode="long_blink_confirm", chalk=use_chalk)
    shell.scene(load_scene(), time.monotonic())
    return shell, arm, log
