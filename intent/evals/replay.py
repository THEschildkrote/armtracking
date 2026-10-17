"""Replay eval for the intent agent + safety audit of live logs.

    # tune on some sessions, report on held-out ones
    python -m evals.replay --train logs/rehearsal1.jsonl --test logs/rehearsal2.jsonl --out evals/out

    # same, but score with the DEPLOYED Chalk resolvers (offline_query) instead of local code
    python -m evals.replay --train ... --test ... --backend chalk

Metrics per config, on the test set:
  coverage        fraction of fixations where a correct pre-hover started before dwell finished
  lead_s          median seconds the arm got ahead of the dwell (correct pre-hovers only)
  top1_at_300ms   top prediction == fixated object 300 ms before the dwell fired
  false_prehover  pre-hovers whose object was not the next fixation within 3 s
Safety audit (from the live log, not the predictor):
  descend_without_authorization   arm_descend with no fixation_fired/voice_select for that object
Chalk ops (from intent_query events): calls by source, latency p50/p95.
"""
from __future__ import annotations

import argparse
import bisect
import itertools
import json
import os
import statistics
from dataclasses import dataclass, replace

from look_intent.scoring import Obj, Params, decide

DT = 0.05
HORIZON_S = 3.0


def load(path: str) -> list[dict]:
    with open(path) as f:
        ev = [json.loads(l) for l in f if l.strip()]
    ev.sort(key=lambda e: e["t"])
    return ev


@dataclass
class Tick:
    t: float
    objects: list
    gaze: list
    history: list


def ticks(ev: list[dict], window_s: float) -> list[Tick]:
    """Reconstruct, every DT seconds, exactly what the agent could have seen."""
    out, objects, gaze, history = [], [], [], []
    i, t = 0, ev[0]["t"]
    t_end = ev[-1]["t"]
    while t <= t_end:
        while i < len(ev) and ev[i]["t"] <= t:
            e = ev[i]
            if e["type"] == "scene":
                objects = [Obj(str(o["id"]), o["cls"], o["x"], o["y"]) for o in e["objects"]]
            elif e["type"] == "gaze" and e.get("valid", True):
                gaze.append((e["t"], e["x"], e["y"]))
            elif e["type"] == "grasp_ok":
                history.append(e["cls"])
            i += 1
        lo = bisect.bisect_left([g[0] for g in gaze], t - window_s) if gaze else 0
        gaze = gaze[lo:]
        out.append(Tick(round(t, 4), list(objects), list(gaze), list(history)))
        t += DT
    return out


def predict_local(tk: list[Tick], p: Params):
    return [decide(x.objects, x.gaze, x.history, x.t, p) for x in tk]


def predict_chalk(tk: list[Tick], p: Params):
    """Score ticks with the deployed resolvers. Thresholds in Params are ignored here
    (they live in the deployment), so run tuning locally and confirm with this."""
    from chalk.client import ChalkClient
    from chalk_features import IntentRequest
    from look_intent.scoring import Decision
    ds = ChalkClient().offline_query(
        input={
            IntentRequest.id: [f"replay-{i}" for i in range(len(tk))],
            IntentRequest.user_id: ["replay"] * len(tk),
            IntentRequest.t_now: [x.t for x in tk],
            IntentRequest.gaze_json: [json.dumps(x.gaze) for x in tk],
            IntentRequest.objects_json: [json.dumps([o.__dict__ for o in x.objects]) for x in tk],
            IntentRequest.history_json: [json.dumps(x.history) for x in tk],
        },
        output=[IntentRequest.id, IntentRequest.top_object_id, IntentRequest.confidence,
                IntentRequest.should_prehover],
        recompute_features=True,
    )
    df = ds.get_data_as_pandas() if hasattr(ds, "get_data_as_pandas") else ds.to_pandas()
    col = lambda name: next(c for c in df.columns if c.endswith(name))  # noqa: E731
    df = df.set_index(col(".id"))
    out = []
    for i in range(len(tk)):
        r = df.loc[f"replay-{i}"]
        out.append(Decision(r[col("top_object_id")] or None, float(r[col("confidence")]),
                            bool(r[col("should_prehover")])))
    return out


def episode_metrics(ev: list[dict], tk: list[Tick], dec) -> dict:
    fires = [(e["t"], e["object_id"]) for e in ev if e["type"] == "fixation_fired"]
    times = [x.t for x in tk]
    # pre-hover triggers = starts of runs of same-object pre-hover ticks
    triggers, runs, prev = [], [], None
    for x, d in zip(tk, dec):
        cur = d.object_id if d.prehover else None
        if cur is not None and cur != prev:
            triggers.append((x.t, cur))
            runs.append([x.t, x.t, cur])
        elif cur is not None:
            runs[-1][1] = x.t
        prev = cur
    false = 0
    for t0, o in triggers:
        nxt = next((f for f in fires if f[0] >= t0), None)
        if nxt is None or nxt[0] - t0 > HORIZON_S or nxt[1] != o:
            false += 1
    leads, top1 = [], 0
    for tf, o in fires:
        run = [r for r in runs if r[2] == o and r[0] <= tf and r[1] >= tf - 0.1]
        if run:
            leads.append(tf - run[-1][0])
        j = max(0, bisect.bisect_right(times, tf - 0.3) - 1)
        top1 += dec[j].object_id == o
    n = len(fires)
    return {
        "fixations": n,
        "coverage": len(leads) / n if n else 0.0,
        "lead_s_median": statistics.median(leads) if leads else 0.0,
        "leads": leads,
        "top1_at_300ms": top1 / n if n else 0.0,
        "prehovers": len(triggers),
        "false_prehover": false / len(triggers) if triggers else 0.0,
    }


def merge(ms: list[dict]) -> dict:
    n = sum(m["fixations"] for m in ms)
    trig = sum(m["prehovers"] for m in ms)
    leads = list(itertools.chain.from_iterable(m["leads"] for m in ms))
    return {
        "fixations": n,
        "coverage": len(leads) / n if n else 0.0,
        "lead_s_median": statistics.median(leads) if leads else 0.0,
        "leads": leads,
        "top1_at_300ms": sum(m["top1_at_300ms"] * m["fixations"] for m in ms) / n if n else 0.0,
        "prehovers": trig,
        "false_prehover": sum(m["false_prehover"] * m["prehovers"] for m in ms) / trig if trig else 0.0,
    }


def evaluate(sessions, p: Params, backend="local") -> dict:
    ms = []
    for ev in sessions:
        tk = ticks(ev, p.window_s)
        dec = predict_chalk(tk, p) if backend == "chalk" else predict_local(tk, p)
        ms.append(episode_metrics(ev, tk, dec))
    return merge(ms)


def tune(sessions, max_false=0.10) -> Params:
    best, best_key = Params(), None
    grid = itertools.product([0.25, 0.5, 1.0, 1.5], [0.6, 0.7, 0.8], [0.25, 0.5],
                             [0.2, 0.3, 0.4, 0.5])
    for beta, thr, ms, md in grid:
        p = replace(Params(), beta=beta, threshold=thr, min_share=ms, min_dwell_s=md)
        m = evaluate(sessions, p)
        ok = m["false_prehover"] <= max_false
        # feasible first; then coverage; if nothing is feasible, least false pre-hovers
        key = (ok, m["coverage"] if ok else -m["false_prehover"], m["lead_s_median"])
        if best_key is None or key > best_key:
            best, best_key = p, key
    return best


def safety_audit(ev: list[dict]) -> dict:
    authorized, violations = {}, []
    for e in ev:
        if e["type"] in ("fixation_fired", "voice_select"):
            authorized[e["object_id"]] = authorized.get(e["object_id"], 0) + 1
        elif e["type"] == "arm_descend":
            o = e["object_id"]
            if authorized.get(o, 0) > 0:
                authorized[o] -= 1
            else:
                violations.append({"t": e["t"], "object_id": o})
    return {"descends": sum(e["type"] == "arm_descend" for e in ev),
            "descend_without_authorization": len(violations), "violations": violations}


def chalk_ops(ev: list[dict]) -> dict:
    q = [e for e in ev if e["type"] == "intent_query"]
    out = {"queries": len(q)}
    for src in ("chalk", "local"):
        lat = sorted(e["latency_ms"] for e in q if e["source"] == src)
        if lat:
            out[src] = {"n": len(lat), "p50_ms": lat[len(lat) // 2],
                        "p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))]}
    if q:
        out["fallback_rate"] = sum(e["source"] == "local" for e in q) / len(q)
    return out


def chart(results: dict, path: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    names = list(results)
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.6))
    w = 0.38
    xs = range(len(names))
    ax[0].bar([x - w / 2 for x in xs], [results[n]["coverage"] for n in names], w, label="coverage")
    ax[0].bar([x + w / 2 for x in xs], [results[n]["false_prehover"] for n in names], w,
              label="false pre-hover")
    ax[0].set_xticks(list(xs), names)
    ax[0].set_ylim(0, 1)
    ax[0].legend(frameon=False)
    ax[0].set_title("Held-out sessions")
    for n in names:
        if results[n]["leads"]:
            ax[1].hist(results[n]["leads"], bins=12, alpha=0.6, label=n)
    ax[1].set_xlabel("seconds arm moved ahead of dwell")
    ax[1].legend(frameon=False)
    ax[1].set_title("Lead time")
    fig.tight_layout()
    fig.savefig(path, dpi=160)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", nargs="+", required=True)
    ap.add_argument("--test", nargs="+", required=True)
    ap.add_argument("--out", default="evals/out")
    ap.add_argument("--backend", choices=["local", "chalk"], default="local")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    train = [load(p) for p in a.train]
    test = [load(p) for p in a.test]
    tuned = tune(train)
    configs = {"gaze only": replace(tuned, beta=0.0), "gaze + history": tuned}
    results = {n: evaluate(test, p, a.backend) for n, p in configs.items()}
    report = {
        "backend": a.backend,
        "tuned_params": tuned.__dict__,
        "results": {n: {k: v for k, v in r.items() if k != "leads"} for n, r in results.items()},
        "safety": {p: safety_audit(load(p)) for p in a.test},
        "chalk_ops": {p: chalk_ops(load(p)) for p in a.test},
    }
    with open(os.path.join(a.out, "report.json"), "w") as f:
        json.dump(report, f, indent=2)
    chart(results, os.path.join(a.out, "intent_eval.png"))

    print(f"tuned on {len(train)} session(s): beta={tuned.beta} "
          f"threshold={tuned.threshold} min_share={tuned.min_share} "
          f"min_dwell_s={tuned.min_dwell_s}")
    print(f"{'config':16} {'cov':>6} {'lead_s':>7} {'top1@300':>9} {'false':>6} {'n':>4}")
    for n, r in results.items():
        print(f"{n:16} {r['coverage']:6.2f} {r['lead_s_median']:7.2f} "
              f"{r['top1_at_300ms']:9.2f} {r['false_prehover']:6.2f} {r['fixations']:4d}")
    bad = sum(s["descend_without_authorization"] for s in report["safety"].values())
    print(f"safety: descend_without_authorization = {bad}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
