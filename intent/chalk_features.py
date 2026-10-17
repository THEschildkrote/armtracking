"""Chalk feature graph for Look's intent agent.

Deploy:   cd intent && chalk apply
Query:    see look_intent/client.py

Three resolvers instead of one so the query-plan view shows the DAG:
  gaze window ──> gaze_shares ─┐
                               ├─> decision (probs, top object, confidence, prehover)
  pick history ─> transition ──┘
The math lives in look_intent/scoring.py and is shared with the local fallback.
"""
import json

from chalk import online
from chalk.features import Features, features

from look_intent.scoring import (Obj, gaze_shares, score, should_prehover,
                                  transition_prior, parse_params)


@features
class IntentRequest:
    id: str
    user_id: str
    t_now: float
    # inputs (sent by the robot laptop)
    gaze_json: str      # [[t, x_mm, y_mm], ...] last ~1.5 s, table frame
    objects_json: str   # [{"id", "cls", "x", "y"}, ...] from the overhead camera
    history_json: str   # ["cup", "spoon", ...] picked classes, oldest first
    corrections_json: str  # past explicit corrections only
    params_json: str       # same config for online, offline and local fallback
    # derived
    shares_json: str
    prior_json: str
    probs_json: str
    top_object_id: str
    confidence: float
    should_prehover: bool


def _objs(objects_json: str) -> list[Obj]:
    return [Obj(str(d["id"]), d["cls"], float(d["x"]), float(d["y"]))
            for d in json.loads(objects_json)]


@online
def resolve_gaze_shares(
    t_now: IntentRequest.t_now,
    gaze_json: IntentRequest.gaze_json,
    objects_json: IntentRequest.objects_json,
    params_json: IntentRequest.params_json,
) -> IntentRequest.shares_json:
    return json.dumps(gaze_shares(json.loads(gaze_json), _objs(objects_json), t_now, parse_params(params_json)))


@online
def resolve_transition_prior(
    history_json: IntentRequest.history_json,
    objects_json: IntentRequest.objects_json,
    corrections_json: IntentRequest.corrections_json,
    params_json: IntentRequest.params_json,
) -> IntentRequest.prior_json:
    classes = [o.cls for o in _objs(objects_json)]
    return json.dumps(transition_prior(json.loads(history_json), classes, parse_params(params_json),
                                       json.loads(corrections_json)))


@online
def resolve_decision(
    t_now: IntentRequest.t_now,
    gaze_json: IntentRequest.gaze_json,
    objects_json: IntentRequest.objects_json,
    shares_json: IntentRequest.shares_json,
    prior_json: IntentRequest.prior_json,
    params_json: IntentRequest.params_json,
) -> Features[
    IntentRequest.probs_json,
    IntentRequest.top_object_id,
    IntentRequest.confidence,
    IntentRequest.should_prehover,
]:
    objs = _objs(objects_json)
    shares = json.loads(shares_json)
    prior = json.loads(prior_json)
    p = parse_params(params_json)
    probs = score(objs, shares, prior, p)
    if not probs:
        return IntentRequest(probs_json="{}", top_object_id="", confidence=0.0,
                             should_prehover=False)
    top = max(probs, key=probs.get)
    conf = probs[top]
    return IntentRequest(
        probs_json=json.dumps(probs),
        top_object_id=top,
        confidence=conf,
        should_prehover=should_prehover(objs, json.loads(gaze_json), shares, prior, top, conf,
                                        t_now, p),
    )
