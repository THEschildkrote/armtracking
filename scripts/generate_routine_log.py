#!/usr/bin/env python3
"""Explicitly synthetic, reproducible 14-day routine; never claimed as patient data."""
import argparse
import csv
import random
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

def generate(path,user='demo',days=14,seed=42,end=None):
    rng=random.Random(seed);zone=ZoneInfo('America/New_York')
    end=end or datetime.now(zone).replace(hour=0,minute=0,second=0,microsecond=0)
    rows=[]
    for day in range(days):
        date=end-timedelta(days=days-day)
        schedule=[(8,'medication'),(8,'mug'),(9,'mug'),(10,'mug'),(13,'water_bottle'),
                  (14,'water_bottle'),(15,'water_bottle'),(16,'water_bottle'),(19,'phone'),(20,'medication'),(21,'phone')]
        for hour,label in schedule:
            if label!='medication' and rng.random()<0.08:continue
            if label!='medication' and rng.random()<0.08:label=rng.choice(['mug','water_bottle','phone'])
            t=date.replace(hour=hour,minute=rng.randint(0,30))
            rows.append([t.isoformat(),user,label,'cancelled' if rng.random()<.08 else 'confirmed','synthetic_demo','true'])
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='') as f:
        w=csv.writer(f,lineterminator="\n");w.writerow(['timestamp','user_id','object_label','status','source','synthetic']);w.writerows(sorted(rows,key=lambda r:r[0]))
    return len(rows)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',default='data/grab_log.csv');p.add_argument('--user',default='demo');p.add_argument('--seed',type=int,default=42)
    a=p.parse_args();print(f'Wrote {generate(a.out,a.user,seed=a.seed)} SYNTHETIC events to {a.out}')
