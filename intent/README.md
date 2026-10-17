# Look intent: Grok Voice → correction memory → Chalk

The calibrated arm is not required to develop this layer. The runnable shell
uses a fake arm, logs its actions, and supports both a synthetic demo and a live
Grok Voice session. Hardware calibration and motion planning remain separate.
See [ARCHITECTURE.md](../ARCHITECTURE.md) for the contracts.

## Quick start (no credentials or hardware)

From the repository root, use Python 3.12:

```sh
PYTHONPATH=intent python -m unittest discover -s intent/tests -v
PYTHONPATH=intent python -m look_intent.demo --out /tmp/look-demo.jsonl
```

The demo simulates a Grok select call correcting cup → spoon. It shows the habit
prior before/after, a validated mock pick, and next-query correction_count=1.
This is synthetic protocol/evaluation evidence, not a live Grok or physical demo.

## Live Grok Voice and optional Chalk

```sh
pip install -r intent/requirements.txt -r intent/requirements-voice.txt
# Set XAI_API_KEY in your environment; do not paste or commit it.
# For Chalk, authenticate with the Chalk CLI or use its credential environment.
cd intent
chalk apply
python -m look_intent.demo --live --chalk --user wearer1 \
  --memory-dir data/wearer1 --out logs/rehearsal1.jsonl
```

Omit `--chalk` to test Grok with local intent first. Deployment must include the
new corrections_json and params_json input features. The client safely falls
back locally if an older deployment cannot resolve them. It reports source and
latency; verify source=chalk before saying Chalk handled a live decision.

The live CLI still uses **MockArm**. It reads scene/gaze/outcome JSONL from stdin
and supplies arrival timestamps on its session clock. For a desk test, paste:

```json
{"type":"scene","objects":[{"id":"c","cls":"cup","x":0,"y":300},{"id":"s","cls":"spoon","x":50,"y":300}]}
{"type":"gaze","x":25,"y":300,"valid":true}
```

The midpoint opens a clarification; the mic opens for your answer after Grok
asks the question. Reply with an object name. Send continuous gaze frames from
the tracker for normal dwell/prediction. To exercise an explicit correction,
open a question with a known suggestion:

```json
{"type":"question","suggested_id":"c","reason":"confirm_selection"}
```

Say “no, the spoon.” It records a voice_correction only if Grok marks the answer
as an explicit correction. No known wrong suggestion means no correction label.
The six tools are select, shift, cancel, release, put_back and repeat_last.

Outcome callbacks (not model output) drive memory and held-object state:

```json
{"type":"grasp_ok","object_id":"s"}
{"type":"delivered","object_id":"s"}
```

A later question can accept release/put_back; a released callback clears held
state. Return locations are validated against the current scene, reach, face
no-go zone and clearance. The physical controller must revalidate all motion.
`{"type":"stop"}` stops the CLI. Device/audio/network errors stop the task.
Logs exclude API keys, raw microphone audio and transcripts.

## Robot integration hooks

Create one Shell with an IntentService, TaskMemory, FeedbackMemory and your arm
adapter. Feed scene(), sample(), grasp_ok(), delivered() and released() from the
pipeline. Run shell.run(stop_event, clock) on a worker thread; it targets 10 Hz.
The adapter methods enqueue commands and return promptly. The default mock
emits arm_descend at dispatch; real hardware must emit at actual descent.

VoiceBridge executes locally validated tool calls. It records correction IDs,
the model query ID, wrong/correct classes and the actual preceding picked class.
FeedbackMemory persists these labelled intent observations per user, separate
from successful physical picks. Every following query sends that snapshot to
Chalk. The graph and local fallback share the same scoring functions.

The 150 ms service wait and SDK timeouts bound cloud waiting. A failed/slow call
opens a five-second breaker; at most one unfinished cloud call is retained.
No cloud result authorizes descent. Local dwell/voice checks and the independent
hardware controller own that decision. Confidence is a heuristic score, not a
calibrated probability or a safety guarantee.

Read shell.status for the top object, confidence, prehover, query_id, source,
latency_ms, correction_count and voice_pending. The live CLI prints this status
to stderr for integration; connect these fields to your dashboard overlay.

## Evaluation

Record separate real rehearsal sessions; tune on train only:

```sh
cd intent
python -m evals.replay --train logs/run1.jsonl --test logs/run2.jsonl --out evals/out
python -m evals.replay --train logs/run1.jsonl --test logs/run2.jsonl \
  --backend chalk --out evals/out-chalk
```

Both models are tuned separately on training sessions. Offline Chalk requests
include their explicit parameters, so beta=0 and beta>0 are real ablations through
the same deployed graph. Feedback is reconstructed only from events available
at each tick. Earlier held-out corrections can affect later held-out decisions;
the report calls this prequential adaptation. Session-start memory snapshots
are logged by the live CLI. Train/test path overlap and exact copies are rejected.

Report coverage, predicted lead before confirmation, top-1, false pre-hover,
correction cases, wrong predictions at corrections, latency/fallback source,
and the authorization audit. Charts describe prediction lead, not measured
physical delivery speed. Corrections explicitly flag wrong guesses even if an
earlier fixation selected that wrong object. Audit tokens are single-use and
expire; production task IDs must propagate through physical descent logs.

Synthetic fixtures from evals.make_fake_log and the demo are explicitly marked.
Use them for development, never as rehearsal results in the pitch. Pure local
runs intentionally have source=local; the reported local fraction is not proof
of a Chalk outage. Deployed SDK result schema and credentials still require a
real online/offline smoke test on-site.

## Cursor and sponsor demo

`.cursor/rules/look-contracts.mdc` references ARCHITECTURE.md. Open the repository
in Cursor and verify the rule is active. Record an actual Cursor Agent edit
against MockArm with passing tests; this Codex implementation alone does not
establish that the project was built using Cursor.

Show: actual Grok answer → voice_correction event → next Chalk query containing
the feedback → graph and latency → held-out evaluation. Correction evidence
updates smoothed habit counts; it does not train Grok or prove improvement by
itself. Grok connects directly to xAI; AI Gateway is not implemented or claimed.
