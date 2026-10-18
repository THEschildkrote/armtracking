# Sponsor integration verification — 2026-10-17

## Implemented and locally verified

- Optional launcher/subclass around the existing `gaze.intent_bridge.BlinkShell` and
  `arm.intent_arm.IntentArm`; original gaze, eye, arm, scene, streams, and intent controller
  source files are unchanged (57 files compared byte-for-byte against baseline).
- xAI Realtime microphone/function-call implementation, strict confirmation gate, blink
  race arbitration, deadlines, cancellation and per-attempt telemetry.
- `chalkpy` routine feature graph; synthetic 145-event dataset; bounded online-query
  adapter and prior/rank debug display; strict live-only two-hour query demo.
- Factory `droid exec` workflow, validated report/suggestion outputs, provenance, explicit
  deployment into Chalk, and an optional real development-review command.
- 24 sponsor tests passed, including SDK resolver execution and simulated failure/race
  cases. All 37 existing intent tests passed. Test doubles are restricted to tests.

## Live services: actual results

**Chalk:** connected and verified with actual successful online queries on the Look team.
Deployment [cmvd0ten7083l013exulu13bp](https://chalk.ai/projects/cmvbwwx3u00eg013krjngoc8k/environments/lkys2e0c880e/deployments/cmvd0ten7083l013exulu13bp)
finished provisioning after the initial 503 responses. Authentication/cold-start took up to
2.8 seconds. A new bounded startup warmup keeps that work outside the control path.
The unmodified 500 ms runtime deadline then succeeded. The actual two-hour demo returned:

- 09:00: mug prior 0.750, water bottle 0.0625; mug ranked first; query 373.8 ms.
- 15:00: water bottle prior 0.750, mug 0.125; water bottle ranked first; query 85.8 ms.

These are real cloud results on explicitly synthetic history and simulated hour buckets.
Logs retain query/correlation IDs. These timings are individual observations, not an SLA.

**Grok:** the replacement key supplied in the user's local file authenticated successfully.
The real xAI Realtime API classified generated speech saying “Yes, grab it” as CONFIRM
in 712 ms after audio commit. This was an API connection test with generated speech,
not a live microphone/hardware rehearsal; no arm command was sent. The first recording
was empty and correctly produced UNCLEAR. TLS certificate verification remains enabled,
using certifi's trust bundle. The private config points to the user-provided key file;
no key is included in source or exported artifacts.

**Factory:** installed official Droid CLI 0.244.0 and verified its SHA-256 checksum.
Real routine-analysis session: `ecb3f76c-897e-4b3c-bfb8-00932a332b6e`.
It produced `reports/routine_summary.md` and `reports/suggested_priors.json` with all
24 buckets. Independently checked its totals: 137 in-window events, 128 confirmed,
9 cancelled. All baseline data is explicitly synthetic; there were no new runtime events.
The broad development review timed out at 120 seconds. A focused review of the
confirmation adapter succeeded in session `64c9a42a-f778-41c9-94b3-eb42a109b08e`
and returned “No findings.” This is development review, not authorship of the modules.
Temporary Factory credential storage was deleted after the runs; no plaintext key was
found in Factory's local cache. Future Factory runs need the key supplied in the environment.

Factory → Chalk handoff is verified: online query metadata identified serving deployment
`cmvd285q508fl01y5amzpungm`, and `agent_priors_json` returned the actual Factory output:
09:00 mug 0.75, water_bottle 0.0625, phone 0.125, medication 0.0625.
The initial empty suggestions came from the previous deployment during rollout; after
the new server finished starting, the same query returned the staged agent suggestions.

**Hardware:** the new adapter has not been exercised on the physical SO-101/cameras in
this environment. Existing core tests and byte comparisons are not a substitute for a
hardware rehearsal.

## Remaining demo rehearsal

The three provider connections and Factory → Chalk data path have been verified.
The physical microphone → voice/blink → arm rehearsal still needs the actual hardware.
The private local config enables the modules and references the user's explicit Grok key
file. Factory is installed at ~/.local/bin/droid. Its key was intentionally not persisted;
future agent runs need FACTORY_API_KEY supplied again. No temporary credential file remains.
