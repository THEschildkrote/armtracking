# Look frontend

The local showcase uses the original black RobotScene component on both Home and
Demo. All four objects are supported: Medicine, Mug, Water, Phone. Core gaze, eye,
relay and physical arm files remain unchanged. This change does not deploy Vercel.

## Interface

Home keeps the iris and original object animations. The requested redundant
illustration caption is removed. The Demo page keeps three camera inputs, then a
compact live Chalk context layer, a large interactive 3D arm and the request trail.
The old recommendation cards, object emojis and microphone/blink/fixture choices
are removed. A user selects an object and uses one **Confirm reach** control.
Factory analysis and evaluation pages show saved outputs without Run Evaluation,
Analyze with Droid or Update Chalk buttons. They do not start hidden agent runs.

## 3D confirmation and completion

Studio object names map to backend labels: medicine -> medication, water ->
water_bottle, mug -> mug, phone -> phone. The original 3D component is interactive,
so its object selector and the scene tabs both establish an explicit selection.
`/api/confirm` only accepts an awaiting-confirmation selection. It returns a unique
`animation_id` and sets the state to grabbing. No motor command is sent.
The original animation's completion event sends that ID to
`/api/animation_complete`; the state becomes delivered only for the matching active
request. Repeated/stale completions cannot finish another action. Selection and
confirmation are disabled during movement. There is no fixed two-second delivery
mock on this new path.

The adapter events are `look:studio-object {moment}`, `look:studio-phase
{phase,animationId}`, `look:studio-select {moment}`, and `look:studio-complete
{moment,animationId}`. Readiness events cover direct links into the studio.

## Quiet, live Chalk context

The studio requests `/api/context` when entered and when the hour changes. It
queries the actual deployed Chalk features for all four objects. On initial load,
the resulting prior determines the starting preview; it never confirms a reach.
An explicit user choice takes precedence. The context layer shows the hour and
observed latency rather than recommendation cards. Calls continue to be logged.

Context has an independent lock/status/error path. Warmup or query failure cannot
lock out select, confirm or animation completion. There are no automatic retry loops
on provider failures. The legacy rank/voice/blink APIs remain available to existing
integrations but are not alternative controls on this page.

## Saved results

Startup loads the most recent valid successful `logs/evaluations/*/evaluation.json`
for the read-only metric cards and bars. Missing results show an empty state, not
fabricated numbers. Factory time-block highlights come from its saved validated
hourly probabilities and identify the synthetic demo source. No agent or deployment
is triggered by rendering either page.

## Cameras and concepts

Three MJPEG URLs can be attached: wrist close-up, overhead workspace, and the user's
view. The relay offers `/wrist`, `/side`, `/front`, `/eye`; `/front` shows what the
user sees and `/eye` shows the eye itself. An overhead camera must actually be
mounted over the table. Feeds are not recorded or used for training here.
The setup survey and corrective video/reinforcement loop remain concepts. The
current physical task is pick-and-place.

## Verification

The sponsor suite passes 50 tests, including confirmation gating, all object labels,
unique request IDs, stale/duplicate completions, saved report loading and context
requests running independently of user actions. JavaScript syntax and evaluation
view checks pass. Browser verification covers Phone selection, original 3D playback,
live Chalk context and removed controls. Source asset provenance is in
`sponsors/showcase/assets/reference/provenance.json`.
