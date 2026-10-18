# Real-world routine context

The supplied `labeled_data.zip` is attributed to the Washington State University
CASAS smart-home datasets: https://casas.wsu.edu/datasets/ . Its exact archive SHA256
is `7d9e976308e1cb5bc91cb39937848bfe073fc7b44c8a33cf166de203014b0091`.
The archive has 81 CSV files and one CSV-formatted TXT file, 1,263,863,673 bytes
uncompressed. `mv001.txt` duplicates `mv001.csv` (verified SHA256) and is excluded.
There are 81 unique recordings, not necessarily 81 distinct people.

Across the unique files: 26,348,650 sensor rows and 319,376 activity BEGIN annotations.
Recorded timestamps span June 15, 2011–April 6, 2023. Rows have date, time, sensor
location/name, sensor state, and optional activity begin/end annotation. Many rows
have no activity label. Examples include Sleep, Drink, Phone, cooking/eating meals,
personal hygiene, watching TV and working. Dates have no explicit timezone offset;
we use each recording's clock hour, not a guessed UTC conversion. These are historical
activity observations, not current calendar schedules, gaze data, object-grab labels,
robot outcomes, or nationally representative survey data.

The importer streams the ZIP without extracting its 1.26GB contents. It counts
activity starts rather than sensor firings. Profiles average smoothed distributions
equally across recordings with observations for each hour, so longer recordings do
not dominate. Missing hourly observations are not evidence of zero activity.

## Reproduce

```
python scripts/import_casas.py /path/to/labeled_data.zip
python scripts/build_casas_prior.py
```

Raw rows remain in the user's archive. The deployable JSON contains aggregate
activity distributions and a frozen split manifest; no raw sensor rows are uploaded.
An archive-wide aggregate is stored locally in the ignored reports directory.
The repository does not redistribute the source archive or assert a license for it.

## Cold start and personalization

Enable `population_enabled` in sponsor config; it defaults off. Every Chalk online
query explicitly supplies the flag. A new-user query uses CASAS activity context
instead of a flat starting prior. Candidate objects are projected using explicit
heuristics: Drink → equal mug/water_bottle support; Phone → phone; Eat/meal labels →
granola as an illustrative food proxy. This mapping is NOT learned object-choice
truth. CASAS cannot distinguish choosing a mug versus a bottle. Other objects,
including medication/inhaler, receive a neutral floor. The mappings are editable in
`intent/look_routine/population.py` and should match the actual inventory.

For each hour bucket, `P(object) = (personal confirmed count + 8 × population prior)
/ (total personal confirmed counts + 8)`. Personal counts use the existing trailing
14-day window. Population influence is 100% at zero confirmations, 50% at eight,
and 20% at 32. Optional Factory suggestion influence also decays as counts grow.
Disabled/missing population data preserves the existing scoring path. Clock/user
inputs and resulting population weight are returned in the runtime evidence.

The dashboard offers a `new-user` profile separately from the synthetic `demo`
profile. Synthetic baseline rows belonging to `demo` do not become the new user's
history. Physical integration must configure the same user ID for logging and
queries. Real confirmed picks are read from `logs/grab_events.csv` on subsequent
queries (subject to cache); virtual showcase grabs intentionally do not fabricate
personal history. Confirmation remains mandatory before physical movement.

## Evaluation: why repeated runs agree

The original button used fixed seeds and unchanged synthetic train/test sessions.
Re-running identical data and code should yield identical metrics; randomness would
hide regressions. A grab CSV cannot replace a complete robotic replay log, which
requires scene, gaze, confirmation and descent events.

When imported CASAS aggregates exist and no custom rehearsal config is supplied,
the button now recomputes a local real-data activity benchmark: 64 training recordings,
17 held-out recordings, 70,789 held-out starts. Only training data creates the deployed
population profile. Metrics are averaged equally over test recordings.

| Predictor | Top-1 activity accuracy | Log loss (lower is better) |
|---|---:|---:|
| Uniform, expected random tie-break | 1.82% | 4.007 |
| Time-independent population | 14.39% | 3.288 |
| Hour-aware population | 15.75% | 3.040 |

These are local activity metrics, not Chalk API performance, object-choice accuracy,
gaze accuracy, physical safety, or robot grasp success. Test data is intentionally
fixed and is not overwritten by live personal picks. New picks affect personalized
runtime probabilities. Reimport changed data to reevaluate a changed corpus. Configure
`sponsors/evaluation.local.json` to run the original real-rehearsal robotic harness,
including its optional Chalk offline backend, instead.

## National context: identified, not integrated

The BLS American Time Use Survey (ATUS) supplies nationally scoped time-use microdata.
Its Activity file contains activity codes, start/stop times and locations. Use the
Respondent file and the appropriate survey weights when building population estimates;
do not treat unweighted diary rows as national probabilities or merge row counts
naively with CASAS sensor observations.

Official data and documentation:
- https://www.bls.gov/tus/data.htm
- https://www.bls.gov/tus/data/datafiles-2025.htm

ATUS has not been imported into this implementation. Accurate Devpost wording today:
“Look starts with time-of-day activity patterns from real CASAS home recordings,
uses an explicit activity-to-object mapping, and gradually personalizes its prior
from the user's confirmed picks.” Do not add “national time-use data” until that
separate integration is implemented and verified.
