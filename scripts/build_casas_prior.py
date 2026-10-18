#!/usr/bin/env python3
"""Build a frozen training-recording prior and a separate real-activity evaluation."""
import hashlib,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'intent'))
from look_routine.population import activity_distribution,OBJECT_MAP

def build(data):
    records=data['recordings'];ids=sorted(records,key=lambda n:hashlib.sha256(n.encode()).hexdigest())
    split=max(1,int(len(ids)*.8));train,test=ids[:split],ids[split:]
    labels=sorted({k for name in train for h in records[name]['hours'] for k in h}|{'__unseen__'})
    pool=[records[n] for n in train]
    hours={str(h):activity_distribution(pool,labels,h) for h in range(24)}
    daily=activity_distribution(pool,labels)
    profile={'schema_version':1,'source':'CASAS','archive_sha256':data['archive_sha256'],
             'source_url':data['source_url'],'method':'Equal-recording average of smoothed activity-start distributions; recorded local clock hours',
             'training_recordings':train,'held_out_recordings':test,'hour_activity_priors':hours,'global_activity_prior':daily,
             'mapping_is_heuristic':True,'object_mapping':OBJECT_MAP,'population_strength':8.0}
    scores={}
    for name,pred in [('Uniform',None),('Time-independent population',daily),('Hour-aware population',hours)]:
        per=[];events=0
        for rid in test:
            total=0;correct=0;nll=0
            for hour,counts in enumerate(records[rid]['hours']):
                q={k:1/len(labels) for k in labels} if pred is None else (pred[str(hour)] if name=='Hour-aware population' else pred)
                top=max(q,key=q.get)
                for activity,n in counts.items():
                    label=activity if activity in q else '__unseen__';total+=n;correct+=n*(1/len(labels) if pred is None else label==top);nll-=n*math.log(q[label])
            if total:per.append((correct/total,nll/total));events+=total
        scores[name]={'recording_macro_top1':sum(x[0] for x in per)/len(per),'recording_macro_log_loss':sum(x[1] for x in per)/len(per),'test_activity_starts':events}
    evaluation={'source':'Real CASAS activity annotations','scope':'Activity-start prediction by hour; NOT object selection, gaze accuracy or robot success',
                'train_recordings':len(train),'test_recordings':len(test),'archive_sha256':data['archive_sha256'],'results':scores}
    return profile,evaluation

if __name__=='__main__':
    data=json.loads((ROOT/'reports/casas_activity_counts.json').read_text());profile,evaluation=build(data)
    (ROOT/'intent/look_routine/data/casas_population.json').write_text(json.dumps(profile,indent=2))
    (ROOT/'reports/casas_evaluation.json').write_text(json.dumps(evaluation,indent=2))
    print(json.dumps(evaluation,indent=2))
