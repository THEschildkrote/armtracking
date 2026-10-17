"""Live cloud-only ranking demonstration; simulated hour is explicitly labelled."""
import argparse
import json
from .config import Config
from .chalk_client import RoutineClient,rank

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--hours',type=int,nargs='+',default=[9,15]);a=p.parse_args()
    client=RoutineClient(Config.load(a.config))
    try:
        for hour in a.hours:
            if not 0<=hour<24:raise ValueError('Hour must be 0..23')
            priors=client.query(['mug','water_bottle','phone','medication'],hour=hour,strict=True)
            print(f'LIVE CHALK | simulated hour {hour:02d}:00 | query {client.last["query_id"]} | {client.last["latency_ms"]} ms')
            rows=rank([('mug','mug',.50),('bottle','water_bottle',.50)],priors,client.config.routine_weight)
            for i,row in enumerate(rows):print(f"  {i+1}. {row['label']:14} gaze={row['gaze']:.2f} prior={row['prior']:.3f} score={row['score']:.3f}")
    finally:client.close()

if __name__=='__main__':main()
