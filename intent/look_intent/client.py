"""IntentService: ask Chalk, fall back to identical local scoring on timeout.

Chalk is NEVER in the safety path. Its output can only trigger a pre-hover
(arm moves above the object, no descent). Descent still requires a completed
dwell fixation or a voice confirmation, enforced by the controller.

Usage (call from a worker thread at ~10 Hz, not the control loop):
    svc = IntentService(user_id="jerry", log=event_logger.write)
    d = svc.predict(objects, gaze_window, memory.history_classes(), t_now)
    if d.prehover: controller.request_prehover(d.object_id)
"""
from __future__ import annotations

import concurrent.futures as cf
import itertools
import json
import time
from typing import Callable, Sequence

from .scoring import Decision, Obj, Params, decide

_ids = itertools.count()


class IntentService:
    def __init__(self, user_id: str, timeout_s: float = 0.15, use_chalk: bool = True,
                 log: Callable[[dict], None] | None = None, params: Params = Params()):
        self.user_id = user_id
        self.timeout_s = timeout_s
        self.params = params
        self.log = log or (lambda e: None)
        self._pool = cf.ThreadPoolExecutor(max_workers=2)
        self._client = None
        self._pending = None
        self._chalk_down_until = 0.0  # circuit breaker after failures
        if use_chalk and params != Params():
            raise ValueError("Deploy matching Chalk parameters before using non-default Params")
        if use_chalk:
            try:
                from chalk.client import ChalkClient  # noqa: WPS433
                self._client = ChalkClient()  # reads CHALK_CLIENT_ID / CHALK_CLIENT_SECRET
            except Exception as e:  # missing package or credentials
                self.log({"type": "intent_chalk_unavailable", "error": repr(e)})

    def _query_chalk(self, objects, gaze, history, t_now) -> Decision:
        from chalk_features import IntentRequest  # import only when Chalk is used
        res = self._client.query(
            input={
                IntentRequest.id: f"{self.user_id}-{next(_ids)}",
                IntentRequest.user_id: self.user_id,
                IntentRequest.t_now: t_now,
                IntentRequest.gaze_json: json.dumps([list(g) for g in gaze]),
                IntentRequest.objects_json: json.dumps(
                    [{"id": o.id, "cls": o.cls, "x": o.x, "y": o.y} for o in objects]),
                IntentRequest.history_json: json.dumps(list(history)),
            },
            output=[IntentRequest.top_object_id, IntentRequest.confidence,
                    IntentRequest.should_prehover, IntentRequest.probs_json],
        )
        top = res.get_feature_value(IntentRequest.top_object_id) or None
        return Decision(
            object_id=top,
            confidence=float(res.get_feature_value(IntentRequest.confidence) or 0.0),
            prehover=bool(res.get_feature_value(IntentRequest.should_prehover)),
            probs=json.loads(res.get_feature_value(IntentRequest.probs_json) or "{}"),
        )

    def predict(self, objects: Sequence[Obj], gaze, history: Sequence[str],
                t_now: float) -> Decision:
        t0 = time.perf_counter()
        source, d = "local", None
        gaze = list(gaze)
        if (self._client is not None and time.monotonic() >= self._chalk_down_until
                and (self._pending is None or self._pending.done())):
            fut = self._pool.submit(self._query_chalk, objects, list(gaze), history, t_now)
            self._pending = fut
            try:
                d = fut.result(timeout=self.timeout_s)
                source = "chalk"
            except Exception as e:
                fut.cancel()
                self._chalk_down_until = time.monotonic() + 5.0  # back off 5 s
                self.log({"type": "intent_chalk_error", "error": repr(e)[:200]})
        if d is None:
            d = decide(objects, gaze, history, t_now, self.params)
        self.log({"type": "intent_query", "t": t_now, "source": source,
                  "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                  "object_id": d.object_id, "confidence": round(d.confidence, 3),
                  "prehover": d.prehover})
        return d

    def close(self):
        """Cancel queued queries; SDK transport must bound any running request."""
        self._pool.shutdown(wait=False, cancel_futures=True)
