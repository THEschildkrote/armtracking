"""Generate a SYNTHETIC session log for developing the eval pipeline.

Synthetic numbers are for testing code only. Never put them in the pitch or Devpost;
report only metrics from real rehearsal logs.

    python -m evals.make_fake_log out.jsonl --seed 1 --tasks 30
"""
from __future__ import annotations

import argparse
import json
import random

OBJECTS = [("cup1", "cup", -120.0, 300.0), ("spoon1", "spoon", 0.0, 340.0),
           ("block1", "block", 120.0, 300.0)]
ROUTINE = ["cup", "spoon", "cup", "block"]  # the user's habit


def generate(seed: int = 0, tasks: int = 30, hz: float = 30.0, noise_mm: float = 12.0,
             habit: float = 0.8, inject_violation: bool = False) -> list[dict]:
    rng = random.Random(seed)
    ev, t = [], 0.0
    objs = {o[0]: {"id": o[0], "cls": o[1], "x": o[2], "y": o[3]} for o in OBJECTS}
    by_cls = {o["cls"]: o for o in objs.values()}
    ev.append({"t": t, "type": "session", "synthetic": True})
    ev.append({"t": t, "type": "scene", "objects": list(objs.values())})

    def gaze_at(x, y, dur):
        nonlocal t
        n = max(1, int(dur * hz))
        for _ in range(n):
            t += 1.0 / hz
            ev.append({"t": round(t, 4), "type": "gaze",
                       "x": x + rng.gauss(0, noise_mm), "y": y + rng.gauss(0, noise_mm)})

    for k in range(tasks):
        cls = ROUTINE[k % len(ROUTINE)] if rng.random() < habit else rng.choice(list(by_cls))
        tgt = by_cls[cls]
        # scan: glances at random spots/objects before committing
        for _ in range(rng.randint(1, 3)):
            o = rng.choice(list(objs.values()))
            gaze_at(o["x"] + rng.gauss(0, 30), o["y"] + rng.gauss(0, 30), rng.uniform(0.15, 0.4))
        # occasionally an aborted dwell on a distractor
        if rng.random() < 0.15:
            d = rng.choice([o for o in objs.values() if o is not tgt])
            ev.append({"t": round(t, 4), "type": "dwell_start", "object_id": d["id"]})
            gaze_at(d["x"], d["y"], 0.4)
            ev.append({"t": round(t, 4), "type": "dwell_cancel", "object_id": d["id"]})
        ev.append({"t": round(t, 4), "type": "dwell_start", "object_id": tgt["id"]})
        gaze_at(tgt["x"], tgt["y"], 0.8)
        ev.append({"t": round(t, 4), "type": "fixation_fired", "object_id": tgt["id"]})
        t += 0.4
        ev.append({"t": round(t, 4), "type": "arm_descend", "object_id": tgt["id"]})
        t += 0.8
        ev.append({"t": round(t, 4), "type": "grasp_ok", "object_id": tgt["id"],
                   "cls": tgt["cls"], "x": tgt["x"], "y": tgt["y"]})
        t += 2.0
        ev.append({"t": round(t, 4), "type": "delivered", "object_id": tgt["id"]})
        gaze_at(0.0, 120.0, rng.uniform(1.0, 2.0))  # looking at the object in hand
        ev.append({"t": round(t, 4), "type": "released", "object_id": tgt["id"]})
        t += 1.5

    if inject_violation:
        ev.append({"t": round(t + 1, 4), "type": "arm_descend", "object_id": "block1"})
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tasks", type=int, default=30)
    ap.add_argument("--inject-violation", action="store_true")
    a = ap.parse_args()
    with open(a.out, "w") as f:
        for e in generate(a.seed, a.tasks, inject_violation=a.inject_violation):
            f.write(json.dumps(e) + "\n")
    print(f"wrote {a.out} (SYNTHETIC)")


if __name__ == "__main__":
    main()
