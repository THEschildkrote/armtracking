"""New, separately named features; existing IntentRequest graph is unchanged."""
import json
from chalk import online
from chalk.features import features, Features
from look_routine.model import DATA, read_events, statistics, prior, validate_suggestions

@features
class RoutineRequest:
    id: str
    user_id: str
    as_of_iso: str
    hour_bucket: int
    timezone: str
    labels_json: str
    recent_events_json: str
    agent_weight: float
    population_enabled: bool
    population_prior_json: str
    population_weight: float
    legacy_prior_json: str
    events_json: str
    counts_7d_json: str
    counts_14d_json: str
    hour_counts_json: str
    hour_frequency_json: str
    agent_priors_json: str
    prior_json: str

@online
def load_routine_log(recent: RoutineRequest.recent_events_json) -> RoutineRequest.events_json:
    # CSV snapshot is included in the deployment bundle; new runtime events arrive as inputs.
    return json.dumps(read_events(DATA/'grab_log.csv')+json.loads(recent))

@online
def routine_counts(events: RoutineRequest.events_json, user: RoutineRequest.user_id,
                   as_of: RoutineRequest.as_of_iso, hour: RoutineRequest.hour_bucket,
                   labels: RoutineRequest.labels_json, zone: RoutineRequest.timezone) -> Features[
                       RoutineRequest.counts_7d_json,RoutineRequest.counts_14d_json,
                       RoutineRequest.hour_counts_json,RoutineRequest.hour_frequency_json]:
    s=statistics(json.loads(events),user,as_of,hour,json.loads(labels),zone)
    return RoutineRequest(counts_7d_json=json.dumps(s['counts_7d']),counts_14d_json=json.dumps(s['counts_14d']),
        hour_counts_json=json.dumps(s['hour_counts']),hour_frequency_json=json.dumps(s['hour_frequency']))

@online
def factory_suggestions(user: RoutineRequest.user_id, as_of: RoutineRequest.as_of_iso,
                        hour: RoutineRequest.hour_bucket) -> RoutineRequest.agent_priors_json:
    path=DATA/'suggested_priors.json'
    if not path.exists():return '{}'
    try:
        data=validate_suggestions(json.loads(path.read_text()),user,as_of)
        return json.dumps(data['hour_priors'].get(str(hour),{}))
    except (ValueError,KeyError,TypeError,OSError):return '{}'

@online
def routine_probability(counts: RoutineRequest.hour_counts_json, agent: RoutineRequest.agent_priors_json,
                        weight: RoutineRequest.agent_weight) -> RoutineRequest.legacy_prior_json:
    return json.dumps(prior(json.loads(counts),alpha=1.0,suggestion=json.loads(agent),weight=weight))


@online
def population_context(enabled: RoutineRequest.population_enabled, counts: RoutineRequest.hour_counts_json,
                       hour: RoutineRequest.hour_bucket) -> RoutineRequest.population_prior_json:
    if not enabled:return '{}'
    from look_routine.population import object_prior
    try:
        profile=json.loads((DATA/'casas_population.json').read_text())
        return json.dumps(object_prior(profile,json.loads(counts),hour))
    except (OSError,ValueError,KeyError,TypeError):return '{}'

@online
def personalized_probability(counts: RoutineRequest.hour_counts_json, population: RoutineRequest.population_prior_json,
                             legacy: RoutineRequest.legacy_prior_json, agent: RoutineRequest.agent_priors_json,
                             weight: RoutineRequest.agent_weight) -> Features[RoutineRequest.prior_json,RoutineRequest.population_weight]:
    from look_routine.population import personalize
    q=json.loads(population);c=json.loads(counts)
    p=personalize(c,q,suggestion=json.loads(agent),agent_weight=weight) if q else json.loads(legacy)
    return RoutineRequest(prior_json=json.dumps(p),population_weight=8/(sum(c.values())+8) if q else 0.0)
