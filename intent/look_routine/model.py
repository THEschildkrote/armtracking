import csv
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(__file__).with_name('data')

def parse_time(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None: raise ValueError('Timestamps must include a timezone')
    return t

def read_events(path):
    with Path(path).open(newline='') as f: return list(csv.DictReader(f))

def statistics(events, user, as_of, bucket, labels, timezone='America/New_York'):
    now=parse_time(as_of)
    if not 0<=bucket<24: raise ValueError('Hour bucket must be 0..23')
    # Count only confirmed intent. Cancelled requests are not positive examples.
    rows=[]
    seen=set()
    for row in events:
        key=tuple(row.get(k,'') for k in ('timestamp','user_id','object_label','status'))
        if key in seen:continue
        seen.add(key)
        if row['user_id']!=user or row['status']!='confirmed':continue
        t=parse_time(row['timestamp'])
        if now-timedelta(days=14)<=t<=now: rows.append((row,t))
    vocabulary=sorted(set(labels)|{r['object_label'] for r,t in rows})
    if not vocabulary:raise ValueError('Empty object vocabulary')
    c7=dict.fromkeys(vocabulary,0);c14=c7.copy();hour=c7.copy()
    zone=ZoneInfo(timezone)
    for r,t in rows:
        label=r['object_label'];c14[label]+=1
        if t>=now-timedelta(days=7):c7[label]+=1
        if t.astimezone(zone).hour==bucket:hour[label]+=1
    total=sum(hour.values())
    return {'counts_7d':c7,'counts_14d':c14,'hour_counts':hour,
            'hour_frequency':{k:v/max(1,total) for k,v in hour.items()}}

def validate_suggestions(data, user, as_of):
    if data.get('schema_version')!=1 or data.get('user_id')!=user:raise ValueError('Suggestion identity/schema mismatch')
    generated=parse_time(data['generated_at']);now=parse_time(as_of)
    if not timedelta(0)<=now-generated<=timedelta(hours=48):raise ValueError('Stale/future suggestions')
    buckets=data['hour_priors']
    if not isinstance(buckets,dict) or not buckets:raise ValueError('Missing hour priors')
    for hour,values in buckets.items():
        if str(int(hour))!=hour or not 0<=int(hour)<24 or not isinstance(values,dict) or not values:
            raise ValueError('Invalid bucket')
        if any(not isinstance(k,str) or not k or isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for k,v in values.items()):
            raise ValueError('Invalid probabilities')
        if abs(sum(values.values())-1)>1e-5:raise ValueError('Probabilities must sum to one')
    return data

def prior(counts, alpha=1.0, suggestion=None, weight=0.0):
    if not math.isfinite(alpha) or alpha<=0 or not 0<=weight<=1:raise ValueError('Invalid smoothing')
    z=sum(counts.values())+alpha*len(counts)
    base={k:(v+alpha)/z for k,v in counts.items()}
    if not suggestion or weight==0:return base
    # Limit suggestions to the data/candidate vocabulary, normalize, retain positive support.
    s={k:max(1e-6,float(suggestion.get(k,0))) for k in base};sz=sum(s.values())
    return {k:(1-weight)*base[k]+weight*s[k]/sz for k in base}
