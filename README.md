# armtracking
Eyetracking arm for accessibility

## Sponsor integrations

Look keeps its gaze → completed dwell → long blink → grab pipeline intact. All sponsor
modules are opt-in, and **no files under `gaze/`, `eye/`, `arm/`, or `scene/` were edited**.
The normal `gaze.cmd` / `python -m gaze.live` entry points retain their existing behavior.
The new `python -m sponsors.launch` entry point injects an adapter into the existing
`make_intent` factory **in its own process**. It imports and reuses `BlinkShell` and
`IntentArm`; it does not copy the camera, pupil detector, calibration, or motor code.

### Setup and configuration

Use the Python environment that already runs your arm and cameras (Python 3.10+):

```bash
python -m pip install -r sponsors/requirements.txt
cp sponsors/config.example.json sponsors/config.local.json
```

All three flags default to `false`. Set `voice_enabled`, `chalk_enabled`, and
`factory_enabled` to `true` in the local config to enable the integrations you want.
Set `arm_port` for the hardware machine and `user_id` for the wearer. Keep the routine
log user ID consistent. The wearer ID is independent of your sponsor account email.

Credentials stay in the local environment; never put keys in config, video, or Git:

```bash
export XAI_API_KEY='your-private-xai-key'
export FACTORY_API_KEY='your-private-factory-key'
chalk login
```

Use your xAI/Factory accounts to create the keys. Chalk also accepts its standard
`CHALK_CLIENT_ID`, `CHALK_CLIENT_SECRET`, and `CHALK_ENVIRONMENT` configuration.
If loading a private `.env` file, export its variables in the terminal before starting
Look. Alternatively, set `xai_key_file` in the private config to the explicit path of a file
containing `XAI_API_KEY=...` or the raw key. The launcher reads only that named file,
without executing its contents. It does not scan your disk for credentials.

The optional dependencies import only when needed. If microphone capture needs PortAudio,
install it for your OS (`brew install portaudio` on macOS), and grant microphone access
to the terminal running Look. Factory's official installer is documented at
[Factory Droid Exec](https://docs.factory.ai/cli/droid-exec/overview); install Droid and
verify `droid exec --help`. Set `DROID_BIN` if it is outside your PATH.

### xAI / Grok: voice as an alternative to blinking

**Real API:** `wss://api.x.ai/v1/realtime?model=grok-voice-latest`, authenticated with
`XAI_API_KEY`. We use xAI's [Realtime Voice API](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech)
with microphone audio and function calling. There is no local keyword matcher posing as Grok.

1. The existing `BlinkShell.sample` completes the original dwell and exposes a candidate.
2. The optional adapter opens a microphone session for that candidate, streams 24 kHz
   mono PCM audio, and asks Grok to call `confirm_candidate` with exactly one of
   `CONFIRM`, `CANCEL`, or `UNCLEAR`.
3. “Yes”, “grab it”, or “that one” can confirm. “No” or “cancel” cancels. Silence,
   malformed tools, conflicting tools, and unclear replies never authorize a grab.
4. Voice confirmation checks the same candidate, scene version, fresh gaze, completed
   dwell, and idle arm, then enters the original `blink_confirm` gate. Blink remains
   active throughout. Both share the controller lock, so the first accepted confirmation
   wins and the second cannot dispatch another grab.
5. A separate debug window displays “Grabbing mug” after acceptance. This text is a
   local acknowledgement of the Grok decision; we do not claim Grok generated that phrase.

Connection/session setup has a 2-second budget per stage. The user has up to 10 seconds
while maintaining gaze to begin answering; after speech ends, the decision has a
2-second deadline. **That is a timeout budget, not a measured latency guarantee.**
Timeout or API/audio failure leaves blink confirmation available. A candidate gets one
voice attempt; look away and dwell again to try again. Changed scenes, lost tracking,
and cancellation invalidate outstanding voice replies.

Each attempt writes `logs/grok_calls.jsonl`: UTC timestamp, call ID, candidate, total
latency, speech-end-to-result latency, result, accepted flag, error class, and whether an
API connection was attempted. Audio, credentials, and transcripts are not logged.
A failed attempt is never labelled successful sponsor usage.

Run with the existing camera arguments:

```bash
python -m sponsors.launch --config sponsors/config.local.json --intent --eye YOUR_EYE_URL --scene YOUR_FRONT_URL
```

The existing calibration and `gaze/objects.json` / `arm/policies.json` setup remain
prerequisites. Use the real hardware launch for the demo; this path contains no MockArm.
Do not use `--arm` or `--blink-action` with the sponsor launcher.

### Chalk: time-of-day routine priors

Generate and deploy a clearly labelled synthetic baseline:

```bash
python scripts/generate_routine_log.py --user demo
python scripts/deploy_routine.py --user demo --project Default
```

The generator writes about two weeks of timestamped, confirmed/cancelled events to
`data/grab_log.csv`, with morning mugs, afternoon water bottles, evening phones, and
medication objects at fixed times. It uses a seeded random generator with occasional
variation and cancellations. **This is synthetic demonstration data, not a patient study.**

`intent/routine_features.py` defines the new `RoutineRequest` graph with `chalkpy`:

| Feature | What Chalk computes |
|---|---|
| `events_json` | Loads the deployed CSV snapshot and merges new runtime events sent by the client. |
| `counts_7d_json`, `counts_14d_json` | Confirmed request counts per wearer and object label over 7/14 days. |
| `hour_counts_json` | Confirmed counts for the requested local hour over the past 14 days. |
| `hour_frequency_json` | Each object's fraction of confirmed requests in that hour. |
| `agent_priors_json` | Validated, fresh Factory suggestions from the deployed JSON, if present. |
| `prior_json` | Laplace-smoothed `P(object | hour)`, optionally blended with Factory suggestions. |

Counts exclude cancelled requests, other wearers, duplicate rows, and future timestamps.
Timestamps include timezones; bucketing uses `timezone` in config (default America/New_York).
The probability is `(hour_count(object) + 1) / (total_hour_count + number_of_labels)`.
Unseen labels therefore retain positive support. JSON maps are keyed by object label;
there is one online request for the entire candidate set, not a network request per object.

The staging script builds a temporary deployment bundle containing **both** the existing
intent graph and new routine graph, then actually runs `chalk apply --force --await=false`. Submission returns promptly;
verify serving readiness with the live ranking demo before recording. It leaves
the original project config and core files unchanged. `--project` must match the project
in your authenticated Chalk account; the verified team uses `Default`. Refresh the
synthetic baseline before a later demo so events are still inside the 14-day window.

During normal use, when at least two objects have recent gaze support, `RoutineClient`
makes an online query. It refreshes at most once per second, caches only that short result,
and computes `gaze_share × prior^routine_weight` to rank those candidates. The separate
Look sponsor window shows each candidate's gaze share, Chalk prior, and rank. Routine
ranking can affect the pre-hover suggestion; **it does not override the core's blink
selection or resolve an ambiguous grasp without the existing dwell checks**.

An asynchronous startup query allows up to 10 seconds for authentication and cold-start
setup; the arm keeps its original scoring during this warmup. The CLI demo waits for
this real startup query before demonstrating the two hours. Thereafter, a 500 ms query deadline runs on the existing intent worker, never on camera processing.
On failure, the adapter returns the original intent decision, allows only one outstanding
query, and waits 5 seconds before trying again. Every attempted query writes
`logs/chalk_queries.jsonl`, including timestamp, query ID, latency, hour, returned priors,
and success/fallback status. A cache hit is not falsely logged as a new API call.

The live ranking demo holds gaze scores equal and changes only the simulated hour:

```bash
python -m sponsors.demo --config sponsors/config.local.json --hours 9 15
```

This command **requires actual successful online responses** and exits on failure.
It labels the hours as simulated and never substitutes local priors. Confirm those queries
in your Chalk dashboard and show the `RoutineRequest` feature graph. This integration
uses features, online resolvers, and online queries; it does not claim native Agent Evals,
AI Gateway, or a streaming connector.

### Factory: routine-analysis workflow and development review

Run a real headless Factory agent:

```bash
bash scripts/run_routine_agent.sh --config sponsors/config.local.json
```

The script invokes `droid exec --output-format json --cwd REPO -f PROMPT` in its default
read-only mode. Droid reads the synthetic `data/grab_log.csv` and any actual
`logs/grab_events.csv` produced by blink/voice confirmation or cancellation. Confirmed
means the user authorized a request; physical success remains in the core arm logs.
Droid summarizes time blocks, counts, and supported changes, and proposes normalized
hourly priors. It must explicitly disclose the synthetic baseline and avoid invented changes.

The Python wrapper validates the real agent response and writes:

- `reports/routine_summary.md`: readable routine findings and notable new observations.
- `reports/suggested_priors.json`: schema, wearer, generation timestamp, all 24 hourly
  distributions, rationale, and provenance (Factory session ID and input file hashes).
- `logs/factory_runs.jsonl` and the raw CLI JSON envelope: actual session evidence,
  latency, success/failure, and input hashes.

A CLI failure, timeout (120 seconds), invalid JSON, invalid probabilities, or unexpected
labels fails the run without manufacturing a report. Existing valid reports remain intact.
Factory does not issue arm commands. The controller continues if this optional script fails.

After a successful run, deploy its suggestions:

```bash
python scripts/deploy_routine.py --user demo --project Default
```

Chalk then blends valid suggestions with empirical probabilities using `agent_weight`
(default 0.2); set it to 0 to use only empirical counts. Suggestions older than 48 hours,
future-dated, or for another wearer are ignored. Redeployment is an explicit step: writing
a local report alone does not update the remote Chalk graph.

For **real development work**, the same workflow has a read-only code review mode:

```bash
bash scripts/run_routine_agent.sh --config sponsors/config.local.json --development-review
```

It asks Droid to review sponsor adapters and tests against the existing controller
contracts and writes `reports/factory_development_review.md`. Only claim Factory
contributed to development after that command successfully produces a real session.

### Judge evidence and demo

See [the 60-second live demo script](docs/SPONSOR_DEMO.md). Sponsor dashboards are the
source of usage verification. Local JSONL files explain what each API call did; they are
not substitutes for dashboard evidence. See [verification status](docs/SPONSOR_VERIFICATION.md)
for what has actually been run, versus what still needs credentials/hardware.

```bash
PYTHONPATH=intent python -m unittest discover -s sponsors/tests -v
PYTHONPATH=intent python -m unittest discover -s intent/tests -v
```

Tests use test doubles to exercise failures and races. The launch, ranking demo, and
Factory analysis commands use the real providers; none has a mocked demo mode.

### Interactive laptop showcase

Run `python -m sponsors.showcase.server --config sponsors/config.local.json` and
open http://127.0.0.1:8765. The dashboard makes real Chalk and Grok calls, can run a
real Factory analysis, and displays request IDs, latencies and serving deployment
metadata. Gaze, clock and arm are explicitly simulated; no motors are commanded.
See [SPONSOR_SHOWCASE.md](SPONSOR_SHOWCASE.md) for recording, provenance, the
60-second narration and the exact hardware handoff dependencies.
