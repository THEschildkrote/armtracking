"""Bounded adapter around the existing robotics replay harness; never controls motors."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from .config import ROOT


def describe(report, source, elapsed):
    lines=['CUSTOM ROBOTICS EVALUATION',f'Data: {source}',
           'Execution: '+('Chalk offline queries' if report['backend']=='chalk' else 'Local replay — no Chalk API call'),
           f'Completed in {elapsed:.1f}s',
           'Protocol: tune on training sessions; measure on separate test sessions.',
           'Scope: existing gaze/history intent scorer, not the hourly routine graph or physical grasp success.','']
    for name,m in report['results'].items():
        n=m['fixations'];pre=m['prehovers']
        lines.extend([name.upper(),f'Confirmed selections evaluated: {n}',
          'Top prediction 300 ms before confirmation: '+(f"{m['top1_at_300ms']:.1%}" if n else 'N/A'),
          'Correct pre-hover coverage: '+(f"{m['coverage']:.1%}" if n else 'N/A'),
          'Median lead before confirmation: '+(f"{m['lead_s_median']:.3f}s" if m['coverage']>0 else 'N/A — no correct pre-hovers'),
          'False pre-hover rate: '+(f"{m['false_prehover']:.1%} ({pre} pre-hovers)" if pre else 'N/A — no pre-hovers'),
          f"Wrong predictions at recorded corrections: {m['wrong_predictions_at_correction']} / {m['correction_cases']}",''])
    a,b=report['results']['gaze only'],report['results']['gaze + history']
    if a['fixations'] and b['fixations']:
        lines.append(f"History vs gaze-only: accuracy {(b['top1_at_300ms']-a['top1_at_300ms'])*100:+.1f} percentage points; coverage {(b['coverage']-a['coverage'])*100:+.1f} points.")
    desc=sum(s['descends'] for s in report['safety'].values());bad=sum(s['descend_without_authorization'] for s in report['safety'].values())
    lines.extend(['',f'Authorization audit: {bad} flagged descents / {desc} logged descents.' if desc else 'Authorization audit: N/A — no descent events.',
                  'This audit checks recorded events, not physical safety certification.'])
    if report.get('synthetic'):lines.append('SYNTHETIC SMOKE TEST ONLY — not measured user or hardware performance.')
    return '\n'.join(lines)


def run():
    started=time.perf_counter();run_id=uuid.uuid4().hex
    out=ROOT/'logs/evaluations'/run_id;out.mkdir(parents=True)
    config_path=ROOT/'sponsors/evaluation.local.json'
    if config_path.exists():
        config=json.loads(config_path.read_text());backend=config.get('backend','local')
        train=[(ROOT/p).resolve() for p in config['train']];test=[(ROOT/p).resolve() for p in config['test']]
        if not train or not test:raise ValueError('Training and test sessions required')
        source='Configured sessions; provenance flags reported below'
    else:
        sys.path.insert(0,str(ROOT/'intent'))
        from evals.make_fake_log import generate
        train=[out/'synthetic-train.jsonl'];test=[out/'synthetic-test.jsonl']
        for path,seed in [(train[0],127),(test[0],932)]:
            path.write_text(''.join(json.dumps(e)+'\n' for e in generate(seed=seed,tasks=6)))
        backend='local';source='Synthetic smoke test (separate generated training/test sessions)'
    if backend not in ('local','chalk'):raise ValueError('Unknown evaluation backend')
    env=dict(os.environ);env['PYTHONPATH']=str(ROOT/'intent')+os.pathsep+env.get('PYTHONPATH','')
    command=[sys.executable,'-m','evals.replay','--train',*map(str,train),'--test',*map(str,test),'--out',str(out),'--backend',backend]
    proc=subprocess.run(command,cwd=ROOT/'intent',env=env,capture_output=True,text=True,timeout=60)
    path=out/'report.json'
    # Exit 1 may represent a completed audit with violations, which must still be shown.
    if not path.exists() or proc.returncode not in (0,1):raise RuntimeError('Evaluation failed; check session format and backend availability')
    report=json.loads(path.read_text());elapsed=round(time.perf_counter()-started,2)
    if report.get('synthetic'):source='Synthetic sessions — smoke-test metrics only'
    elif report.get('mock_arm'):source='Mock-arm sessions — not physical robot performance'
    provenance={str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in train+test}
    result={'run_id':run_id,'elapsed_s':elapsed,'text':describe(report,source,elapsed),'report':report,'input_sha256':provenance}
    (out/'summary.txt').write_text(result['text']+'\n')
    (out/'evaluation.json').write_text(json.dumps(result,indent=2))
    return result
