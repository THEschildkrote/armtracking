# Look frontend

The working sponsor showcase remains at http://127.0.0.1:8765/ and uses the existing
backend actions and confirmation gate. Core gaze, eye, stream relay and arm code is
unchanged. The public Vercel site is not modified by these local changes.

## Pages

- Home: interactive iris, original black-arm Medicine / Mug / Water / Phone previews,
  and a short eye tracking / routine context / voice / robotic reach explanation.
- Demo: separate wrist, overhead-workspace and user-perspective video inputs;
  Chalk recommendations; voice or blink confirmation; virtual arm and request trail.
- Routines: CASAS facts with prominent labels, Factory hourly highlights, analysis and
  deployment controls. Full report text is optional; session IDs are not foregrounded.
- Evaluation: cards and bars generated from the returned custom-harness report, plus
  a collapsible full text report. Activity and robotic-replay metrics have distinct
  labels; missing observations show N/A and negative improvements remain visible.

## Original arm animations

The project owner's requested original animation is reused from its compiled public
RobotScene module at https://look-website-kappa.vercel.app/. Asset hashes, attribution
and the small renderer adaptation are in `sponsors/showcase/assets/reference/`.
The original site application is not mounted. `assets/animation.js` wraps its arm
component with local object selection and play/replay controls. The browser requires
WebGL for this 3D preview and shows the original fallback if it is unavailable.
The animation is illustrative and never sends motor commands.

## Video streams

Open **Connect video streams** and enter MJPEG URLs. These are displayed directly in
browser images; there is no recording, server-side camera access or training step.
Blank fields stay disconnected. Embedded credentials and non-HTTP(S) URLs are rejected.
The existing relay provides `/wrist`, `/side`, `/front` and `/eye`. Use `/front` for
what the user sees; `/eye` shows the eye itself. An overhead source must actually be
mounted over the workspace; labeling a relay URL does not change its camera angle.
Only the static showcase asset directory is served, with resolved-path containment
checks. Local config, private logs and credentials are not exposed as static files.

## Implemented versus conceptual

Real sponsor requests, CASAS starting context, confirmed-count personalization and
custom evaluations already exist. The current robot task is pick-and-place. The
setup survey and corrective video / reinforcement-learning loop are explicitly
labeled **Concept**, as requested; no survey-derived ranking or online policy training
is claimed. Demo gaze and robot movement remain simulated.

Factory's visible time-block highlights are calculated from its saved hourly
probabilities; they are identified as synthetic demo history. CASAS is a separate
population-prior input, not represented as data already analyzed by Droid.

## Verification

Sponsor tests: 39 pass. The JavaScript evaluation-view checks cover real activity
comparisons, missing observations, negative changes and invalid metrics. Inline
scripts pass Node syntax checks. In-browser verification confirms the real CASAS
metrics and restores the original 3D arm and object variants. No synthetic camera
frames or fabricated evaluation values are inserted into the live demo.
