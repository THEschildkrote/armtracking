# intent/ — Chalk intent agent, task memory, evals

The arm starts moving toward what you're about to pick **before** your 0.8 s dwell
finishes, using live gaze plus your pick history. Chalk serves the features and
decision; an identical local copy takes over if Chalk is slow (>150 ms) or down.
**Chalk is never in the safety path:** its output can only trigger a pre-hover.
Descent still needs a completed dwell or a voice confirmation.

```
gaze window ──> gaze_shares ─┐
                             ├─> decision: top object, confidence, pre-hover?
pick history ─> transition ──┘        (habit lowers the dwell bar, never removes it)
```

## Setup
```bash
pip install -r intent/requirements.txt
chalk login                 # or set CHALK_CLIENT_ID / CHALK_CLIENT_SECRET
cd intent && chalk apply    # deploys chalk_features.py
python -m unittest tests.test_intent
```

## Wiring it into the controller (3 hooks)

**1. Log these events** to the session JSONL (`t` = seconds, positions in table mm):

| type | fields |
|---|---|
| `scene` | `objects: [{id, cls, x, y}]` (on change) |
| `gaze` | `x, y, valid` |
| `dwell_start` / `dwell_cancel` / `fixation_fired` | `object_id` |
| `voice_select` | `object_id` (voice confirmation, incl. "do that again") |
| `arm_descend` | `object_id` |
| `grasp_ok` | `object_id, cls, x, y` (where it was picked from) |
| `delivered` / `released` | `object_id` |

`intent_query` events are written by `IntentService` itself.

**2. Intent worker thread (~10 Hz, not the control loop):**
```python
from look_intent.client import IntentService
from look_intent.memory import TaskMemory
memory = TaskMemory("data/task_memory.json")
svc = IntentService(user_id="demo", log=logger.write)
d = svc.predict(scene_objects, gaze_last_1_5s, memory.history_classes(), t_now)
if d.prehover:
    controller.request_prehover(d.object_id)   # hover only; never descend from here
```

**3. Task memory** — on `grasp_ok`: `memory.record_pick(t, id, cls, x, y)`.
Grok voice tools:
- `put_back(object_id)` → `memory.put_back_target(object_id, scene)` returns a spot
  or a speakable reason ("Something is in its old spot now: the block.").
  Reach, no-go zone and clearance are checked locally against the *current* scene.
- `repeat_last()` → `memory.last_task()`; log it as `voice_select` so the audit counts it.

## Evals (the Chalk prize evidence)
```bash
python -m evals.replay --train logs/run1.jsonl logs/run2.jsonl --test logs/run3.jsonl
python -m evals.replay ... --backend chalk   # same replay through the deployed resolvers
```
Tunes on train sessions, reports on held-out ones, gaze-only vs gaze+history:
coverage, median lead time, top-1 at 300 ms before dwell, false pre-hover rate.
Also audits the live log (`arm_descend` without authorization must be 0; exits 1
otherwise) and Chalk latency / fallback rate. Writes `evals/out/report.json` and
`intent_eval.png`.

`python -m evals.make_fake_log` makes **synthetic** logs for testing the pipeline.
Never put synthetic numbers in the pitch; report only rehearsal logs.

## Confirm with the Chalk team at the booth
- `offline_query` result column names (`predict_chalk` matches by suffix).
- Upgrade path: move pick history into Chalk's online store (`User` has-many `Pick`)
  so habits persist across devices instead of being sent with each request.
