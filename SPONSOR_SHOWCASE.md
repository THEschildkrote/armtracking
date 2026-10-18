# Live laptop showcase

Run the dashboard on the same machine as the keys and Chalk CLI configuration:

```sh
python -m sponsors.showcase.server --config sponsors/config.local.json
```

Open http://127.0.0.1:8765. It binds loopback only. No API key is sent to the browser.
For a fresh Factory run, set `FACTORY_API_KEY` in the launching shell or add
`--factory-key-file /absolute/path/to/private-file`. The file can contain a raw key
or `FACTORY_API_KEY=...`; it is parsed without executing shell code. A shared
file with separate raw `xai-...` and `fk-...` lines is also supported; provider
prefixes keep the two credentials separate.

## What is real

- Morning and afternoon buttons call the deployed Chalk graph, using the production
  RoutineClient. Both candidate gaze scores are 0.5. The display includes the true
  query ID, latency, serving deployment ID and whether Factory priors were loaded.
- The speech-fixture button sends PCM audio to xAI Realtime and waits for a completed,
  single `confirm_candidate` tool call. The input is generated speech, explicitly
  labeled. The microphone button records your own audio locally and sends it when
  you stop. Audio is not persisted by this app. The recording script captures no audio.
- Run Droid invokes the real Factory CLI, reads the CSVs, validates its response and
  writes the reports. A prior report is marked historical; it is never presented as
  a fresh call. The report uses a synthetic baseline and discloses that fact.
- Deploy priors runs the existing deployment script. Submission is not readiness.
  Query again after rollout and inspect the serving deployment ID. The dashboard's
  Factory session identifies the local report; it does not prove that a newer local
  report has already reached the serving deployment.

## What is simulated

Gaze scores, dwell selection, blink button, clock hour, and arm animation. The app
never imports a motor controller. Simulated confirmations are excluded from real
runtime grab history. The virtual gate is a presentation abstraction, not an end-to-end
hardware test or a reproduction of all core gaze-validity/concurrency checks.
The physical launcher remains `python -m sponsors.launch --config ... --intent ...`.

## 60-second presentation

1. 0–10s: Show the Factory report's real session and timestamp; disclose synthetic data.
2. 10–25s: Query 09:00 then 15:00. Equal gaze, different live priors and recommendation.
3. 25–40s: Select water bottle to simulate completed dwell. Send generated speech or
   use the microphone. Show Grok's tool result and decision latency.
4. 40–50s: Show the virtual delivery and request trail. State that only confirmation
   authorizes movement; priors alone do not.
5. 50–60s: Show the serving Chalk deployment ID and Factory-prior presence. Explain
   the remaining hardware handoff below.

Routine runs now use a 45-second timeout. They receive the CSV contents inline, use
low reasoning effort, and return compact hourly arrays expanded into the same validated
priors schema. Record a fresh run separately in real time and
label any edit/time cut. Do not claim the historical report was generated live.

## Browser recording

Requires Node, Playwright, Chrome and ffmpeg already installed. Set `CHROME_BIN`,
`FFMPEG_BIN` if paths differ. This laptop's existing tool paths are the defaults.
With the dashboard running:

```sh
node scripts/record_showcase.cjs outputs/showcase
```

The recorder opens Chrome and captures the actual browser via CDP at 10 fps, makes
fresh Chalk and Grok calls, and saves `look-live-browser.mp4`, a screenshot and JSON
request evidence. It stops on failed API results. It does not record audio.
Set `RUN_FACTORY=1` to additionally run Factory (requires server-side credentials;
may add up to 45 seconds). It does not automatically redeploy a fresh Factory report.

In the Codex restricted session Chrome exited with SIGABRT before rendering.
Run the recorder in the Mac Terminal. No screen recording was fabricated to cover
that restriction. Source checks passed; browser layout and recorder execution need
verification from that run.

## Physical handoff — required, not verified remotely

| Dependency | Existing contract / file | Ready when |
|---|---|---|
| Eye and scene streams | `gaze/live.py`, relay `/eye` + `/front` | Frames, valid pupil, blink events and current table homography arrive |
| Gaze calibration | `gaze/calib.json`, `scene/config.json` | Pupil → scene pixel → table millimetres agrees with actual table |
| Robot calibration | `arm/table_to_robot.json`, home pose, reach checks | Marker fit and drop location validated after board/arm movement |
| Object inventory | `gaze/objects.json`, `arm/policies.json` | Same stable object IDs; fresh table positions; each target has a tested scripted grasp or policy |
| Label alignment | `object.cls`, grab log and Factory vocabulary | Hardware labels replace/extend synthetic mug/phone demo data; never alias an unrelated physical object just for the demo |
| Arm execution | `arm/intent_arm.py` | `request_prehover(id)`, `grasp(id)`, `stop()` work on the actual configured port |
| Outcome callbacks | `on_grasp_ok(id,t,verified)`, `on_grasp_failed(id,t,reason)` | Success/failure clears the correct task and permits the next action |
| Operator controls | Existing double blink, stop/home | Cancellation and emergency stop checked physically; duplicate voice/blink cannot issue two grabs |
| Host placement | `sponsors.launch` runs beside the hardware process | Windows COM4 is not a Mac serial port; install sponsor dependencies and credentials on the actual arm host |

The current venue inventory mentions granola, pipe_cleaner, inhaler and water_bottle;
the routine demo uses mug, water_bottle, phone and medication. This label mismatch
must be resolved with the real inventory before claiming useful learned routines
on every hardware object. The laptop browser is not a remote motor-control bridge.

No existing `gaze/`, `eye/`, `scene/`, `streams/`, `arm/`, or `intent/look_intent/`
file was edited for this showcase.

## Verified live run (2026-10-17)

- Morning query `8a2b76f920ab42e2aef3bb4e623feb1a`: 136.4 ms, mug prior 0.75.
- Afternoon query `29851c6316ba4033a4578df07025b915`: 125.0 ms, water bottle prior 0.75.
- Serving deployment for both: `cmvd285q508fl01y5amzpungm`; cloud output included
  nonempty Factory priors for the requested hour.
- Grok request `6dfc103dc18a49569670172deb5784e5`: CONFIRM, 588 ms after audio
  commit, 2005 ms total including connection/setup; generated audio input.
- Virtual arm reached delivered. No physical arm was commanded. Real HTTP API
  sequence passed; browser rendering/recording remains unverified in this sandbox.
