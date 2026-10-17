# 60-second live sponsor demo

## Before recording

- Calibrate and verify the original gaze → blink → grab pipeline on the physical arm.
- Install/authenticate all three services. Enable the three flags in the private config.
- Generate the synthetic log, deploy Chalk, and verify both live ranking queries succeed.
- Run the Factory analysis once to verify access and measure duration. Its runtime varies;
  do not promise a fresh session completes within 60 seconds if the rehearsal did not.
- Open the real xAI usage view, Chalk graph/query view, and Factory session view in tabs.
- Position a named physical object already configured in `arm/policies.json`.
- Use the sponsor launcher with the working camera URLs. Keep the stop control reachable.
- Have two terminals ready: Factory analysis and the Chalk ranking demo. Show no API keys.

## Shot list and narration

| Time | Live action | Narration |
|---|---|---|
| 0–5 s | Start `bash scripts/run_routine_agent.sh --config sponsors/config.local.json` in a visible terminal. Leave it running while demonstrating the arm. | “Look turns gaze into physical assistance. Factory is now analyzing our labelled synthetic routine and today's confirmed or cancelled requests.” |
| 5–20 s | Look at the configured object until dwell completes. Say “yes, grab it.” Show the real arm and “Grabbing…” acknowledgement. | “Grok hears the answer through its Voice API. Either voice or a long blink confirms; the first accepted input wins.” |
| 20–27 s | Show the successful `grok_calls.jsonl` entry and xAI usage. If convenient, demonstrate blink on a second ready object. | “The voice decision is logged, and blinking remains available if the service fails.” |
| 27–42 s | Run `python -m sponsors.demo --config sponsors/config.local.json --hours 9 15`. Show the two results and Chalk dashboard query/graph. | “These are live Chalk queries with simulated times. The same gaze scores favor the mug at nine and water bottle at three, based on two weeks of synthetic habits.” |
| 42–55 s | Return to Factory. Open the freshly written `reports/routine_summary.md`, suggested priors, and real session ID. | “Factory summarizes the routine and proposes hourly priors. We validate its output; the next deployment lets Chalk consume those suggestions.” |
| 55–60 s | End on the arm, with the three real usage views visible as small inserts. | “Gaze chooses, the user confirms, and routine data improves the suggestion.” |

If Factory has not completed at 42 seconds, keep recording until it finishes and shorten
another shot or transparently time-compress the wait. Never replace an unfinished live
run with a fabricated success. The 60-second edit can include cuts; label sped-up portions.
Chalk redeployment after the Factory run may take longer than the video. Do it before a
second take if showing the full Factory → Chalk feedback loop, and retain its real session
ID and deployment evidence. The core arm is never controlled by Factory suggestions alone.
