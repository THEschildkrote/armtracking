#!/usr/bin/env python3
"""Stage data then deploy the existing Chalk project plus the new feature graph."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import os
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'intent'))
from look_routine.model import DATA,read_events,validate_suggestions
from datetime import datetime,timezone

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage-only',action='store_true');p.add_argument('--user',default='demo');p.add_argument('--project',default=os.environ.get('CHALK_PROJECT','Default'));a=p.parse_args()
    source=ROOT/'data/grab_log.csv'
    read_events(source)
    DATA.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,DATA/'grab_log.csv')
    suggestions=ROOT/'reports/suggested_priors.json'
    if suggestions.exists():
        validate_suggestions(json.loads(suggestions.read_text()),a.user,datetime.now(timezone.utc).isoformat())
        shutil.copyfile(suggestions,DATA/'suggested_priors.json')
    elif (DATA/'suggested_priors.json').exists():(DATA/'suggested_priors.json').unlink()
    print('Staged routine data in',DATA)
    if not a.stage_only:
        # Deploy a clean bundle, preserving original intent/chalk.yaml and every core file.
        # Account project names can differ from the old checkout's look-intent placeholder.
        with tempfile.TemporaryDirectory(prefix='look-chalk-') as tmp:
            bundle=Path(tmp)
            for name in ('look_intent','look_routine'):
                shutil.copytree(ROOT/'intent'/name,bundle/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            for name in ('chalk_features.py','routine_features.py','requirements-chalk.txt'):
                shutil.copyfile(ROOT/'intent'/name,bundle/name)
            (bundle/'chalk.yaml').write_text('project: '+json.dumps(a.project)+'\n' +
                'environments:\n  default:\n    requirements: requirements-chalk.txt\n')
            subprocess.run(['chalk','apply','--force','--await=false','--no-spinners','--skip-version-check'],cwd=bundle,check=True,timeout=60)
            print('Deployment submitted. Verify readiness with python -m sponsors.demo; upload is not proof of serving.')

if __name__=='__main__':main()
