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
- 22 sponsor tests passed, including SDK resolver execution and simulated failure/race
  cases. All 37 existing intent tests passed. Test doubles are restricted to tests.

## Live services: actual results

**Chalk:** authenticated against Jerry Dong's existing Look team, using the account
approved in this conversation. `chalk apply` passed validation and uploaded deployment
[cmvd0ten7083l013exulu13bp](https://chalk.ai/projects/cmvbwwx3u00eg013krjngoc8k/environments/lkys2e0c880e/deployments/cmvd0ten7083l013exulu13bp).
The CLI reached “Deploying query servers” but did not finish within the initial wait.
A subsequent actual online request returned HTTP 503. Earlier 500 ms request timed out.
These are failed live attempts, **not successful prior results**. Logs are in
`logs/chalk_queries.jsonl` locally. No numerical live gain or latency success is claimed.
Open the deployment view to resolve/wait for provisioning, then run the strict ranking demo.

**Grok:** no `XAI_API_KEY` was available to this process. No live speech session was
verified, and no xAI usage is claimed. Configure the key on the hardware machine, launch
with voice enabled, speak to a dwelled candidate, and check both accepted telemetry and
xAI's dashboard.

**Factory:** no Factory credentials or existing Droid CLI were available. The official
CLI binary download timed out and retries were stopped. No real Factory routine analysis
or development review ran. No fake reports were created. Install/authenticate Droid and
run both commands from the README before claiming Factory usage.

**Hardware:** the new adapter has not been exercised on the physical SO-101/cameras in
this environment. Existing core tests and byte comparisons are not a substitute for a
hardware rehearsal.

## Completion checklist on the demo machine

1. Confirm original blink-only hardware flow.
2. Add xAI/Factory credentials locally and enable desired flags in private config.
3. Confirm Chalk deployment is serving; run the live two-hour demo successfully.
4. Rehearse voice and blink confirmations on the real arm, including cancellation.
5. Run Factory routine analysis and development review; inspect actual session IDs.
6. Deploy validated Factory suggestions and verify another actual Chalk online query.
7. Record only successful live behavior and honest dashboard evidence.
