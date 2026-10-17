"""Intent scoring. Pure Python, no dependencies.

The SAME functions run inside the Chalk resolvers (chalk_features.py) and in the
local fallback (client.py), so a Chalk outage changes latency, never behaviour.

Model, per candidate object o:
    share_o  = recency-weighted fraction of recent gaze samples near o
    prior_o  = P(class of o | last picked class), Laplace-smoothed
    logit_o  = log(share_o + eps) + beta * log(prior_o)
    p_o      = softmax(logit)
Pre-hover only if max p >= threshold AND share >= min_share AND the eye has
stayed on that object for >= min_dwell_s right now (never on history alone,
never on a passing glance). Habit lowers the evidence bar, never removes it:
    required_dwell = clamp(min_dwell_s * (1/K / prior_top) ** beta, floor_dwell_s, 2*min_dwell_s)
so an expected object pre-hovers sooner. The full 0.8 s dwell is still required
to descend.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Obj:
    id: str
    cls: str
    x: float  # table mm
    y: float  # table mm


@dataclass(frozen=True)
class Params:
    window_s: float = 1.5      # gaze history considered
    tau_s: float = 0.5         # recency decay
    radius_mm: float = 60.0    # gaze-to-object association radius (matches R4)
    alpha: float = 1.0         # Laplace smoothing for transitions
    beta: float = 0.5          # weight of history prior (0 = gaze only)
    eps: float = 0.02          # floor on gaze share
    threshold: float = 0.7     # pre-hover confidence (PRD P1)
    min_share: float = 0.25    # minimum gaze evidence for pre-hover
    min_dwell_s: float = 0.3   # continuous on-object time before pre-hover
    max_gap_s: float = 0.12    # tracking gaps break continuous dwell
    floor_dwell_s: float = 0.15  # never pre-hover on less than this, whatever the habit


@dataclass(frozen=True)
class Decision:
    object_id: str | None
    confidence: float
    prehover: bool
    probs: dict = field(default_factory=dict)


def gaze_shares(gaze: Iterable[Sequence[float]], objects: Sequence[Obj], t_now: float,
                p: Params = Params()) -> dict[str, float]:
    """gaze: iterable of (t, x, y) in table mm. Returns {object_id: share in [0,1]}."""
    shares = {o.id: 0.0 for o in objects}
    total = 0.0
    r2 = p.radius_mm ** 2
    for t, x, y in gaze:
        age = t_now - t
        if age < 0 or age > p.window_s:
            continue
        w = math.exp(-age / p.tau_s)
        total += w
        best, best_d2 = None, r2
        for o in objects:
            d2 = (o.x - x) ** 2 + (o.y - y) ** 2
            if d2 <= best_d2:
                best, best_d2 = o.id, d2
        if best is not None:
            shares[best] += w
    if total > 0:
        shares = {k: v / total for k, v in shares.items()}
    return shares


def current_dwell(gaze: Sequence[Sequence[float]], obj: Obj, t_now: float,
                  p: Params = Params()) -> float:
    """Seconds the gaze has stayed continuously within radius of obj, ending now."""
    r2 = p.radius_mm ** 2
    start = None
    previous = t_now
    for t, x, y in reversed(list(gaze)):
        if t > t_now:
            continue
        if previous - t > p.max_gap_s or not all(math.isfinite(v) for v in (t, x, y)):
            break
        previous = t
        if (obj.x - x) ** 2 + (obj.y - y) ** 2 > r2:
            break
        start = t
    return 0.0 if start is None else t_now - start


def transition_prior(history: Sequence[str], classes: Sequence[str],
                     p: Params = Params(), corrections=()) -> dict[str, float]:
    """history: picked classes, oldest first. Returns P(next class | last class)."""
    classes = sorted(set(classes))
    k = len(classes)
    if k == 0:
        return {}
    last = history[-1] if history else None
    counts = {c: 0 for c in classes}
    n = 0
    for a, b in zip(history, history[1:]):
        if a == last and b in counts:
            counts[b] += 1
            n += 1
    # User-confirmed next-intent labels, without advancing successful-pick history.
    for c in corrections:
        if c.get("previous_cls") == last and c.get("correct_cls") in counts:
            counts[c["correct_cls"]] += 1
            n += 1
    return {c: (counts[c] + p.alpha) / (n + p.alpha * k) for c in classes}


def score(objects: Sequence[Obj], shares: dict[str, float], prior: dict[str, float],
          p: Params = Params()) -> dict[str, float]:
    if not objects:
        return {}
    logits = {}
    for o in objects:
        pr = prior.get(o.cls, 1.0 / max(len(prior), 1))
        logits[o.id] = math.log(shares.get(o.id, 0.0) + p.eps) + p.beta * math.log(pr)
    m = max(logits.values())
    ex = {k: math.exp(v - m) for k, v in logits.items()}
    z = sum(ex.values())
    return {k: v / z for k, v in ex.items()}


def decide(objects: Sequence[Obj], gaze, history: Sequence[str], t_now: float,
           p: Params = Params(), corrections=()) -> Decision:
    shares = gaze_shares(gaze, objects, t_now, p)
    prior = transition_prior(history, [o.cls for o in objects], p, corrections)
    probs = score(objects, shares, prior, p)
    if not probs:
        return Decision(None, 0.0, False, {})
    top = max(probs, key=probs.get)
    conf = probs[top]
    return Decision(top, conf,
                    should_prehover(objects, gaze, shares, prior, top, conf, t_now, p), probs)


def required_dwell(prior: dict[str, float], cls: str, p: Params = Params()) -> float:
    k = max(len(prior), 1)
    pr = prior.get(cls, 1.0 / k)
    d = p.min_dwell_s * ((1.0 / k) / pr) ** p.beta
    return min(max(d, p.floor_dwell_s), 2 * p.min_dwell_s)


def should_prehover(objects, gaze, shares, prior, top, conf, t_now,
                    p: Params = Params()) -> bool:
    if conf < p.threshold or shares.get(top, 0.0) < p.min_share:
        return False
    obj = next(o for o in objects if o.id == top)
    return current_dwell(gaze, obj, t_now, p) >= required_dwell(prior, obj.cls, p)


def params_json(p: Params) -> str:
    import json
    return json.dumps(asdict(p), sort_keys=True, allow_nan=False)


def parse_params(raw: str) -> Params:
    import json
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {f.name for f in fields(Params)}:
        raise ValueError("Params must contain exactly the supported fields")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in data.values()):
        raise ValueError("Params must be finite numbers")
    p = Params(**data)
    if (min(p.window_s, p.tau_s, p.radius_mm, p.alpha, p.eps, p.max_gap_s) <= 0
            or p.beta < 0 or not 0 <= p.threshold <= 1 or not 0 <= p.min_share <= 1
            or p.floor_dwell_s < 0.15 or p.min_dwell_s < p.floor_dwell_s):
        raise ValueError("Invalid scoring parameters")
    return p
