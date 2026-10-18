"""CASAS activity priors and explicit, heuristic activity-to-object projection."""
import math
from .model import prior

# Design assumptions, NOT object-choice labels measured by CASAS.
OBJECT_MAP={'Drink':{'water_bottle':0.5,'mug':0.5},'Phone':{'phone':1.0},
            'Eat':{'granola':1.0},'Eat_Breakfast':{'granola':1.0},'Eat_Lunch':{'granola':1.0},'Eat_Dinner':{'granola':1.0}}

def activity_distribution(recordings, labels, hour=None):
    out=dict.fromkeys(labels,0.0);used=0
    for r in recordings:
        counts={k:0 for k in labels}
        for h in (range(24) if hour is None else [hour]):
            for k,v in r['hours'][h].items():counts[k if k in counts else '__unseen__']+=v
        total=sum(counts.values())
        if not total:continue
        used+=1
        for k in out:out[k]+=(counts[k]+0.5)/(total+0.5*len(labels))
    return {k:v/used for k,v in out.items()} if used else {k:1/len(labels) for k in labels}

def object_prior(profile, labels, hour):
    labels=sorted(set(labels));activities=profile['hour_activity_priors'][str(hour)]
    scores={label:1/len(labels) for label in labels}
    for activity,probability in activities.items():
        for label,weight in OBJECT_MAP.get(activity,{}).items():
            if label in scores:scores[label]+=probability*weight
    total=sum(scores.values())
    return {k:v/total for k,v in scores.items()}

def personalize(counts, population, strength=8.0, suggestion=None, agent_weight=0.0):
    if not population:return prior(counts,suggestion=suggestion,weight=agent_weight)
    if not math.isfinite(strength) or strength<=0:raise ValueError('Invalid population strength')
    if set(population)!=set(counts):raise ValueError('Population vocabulary mismatch')
    if any(not math.isfinite(v) or v<=0 for v in population.values()) or abs(sum(population.values())-1)>1e-5:raise ValueError('Invalid population prior')
    n=sum(counts.values());base={k:(v+strength*population[k])/(n+strength) for k,v in counts.items()}
    # Factory's optional influence also vanishes as personal evidence accumulates.
    weight=agent_weight*strength/(n+strength)
    if suggestion and weight:
        s={k:max(1e-6,float(suggestion.get(k,0))) for k in counts};total=sum(s.values())
        return {k:(1-weight)*base[k]+weight*s[k]/total for k in counts}
    return base
