"""Minimal publishable receipts; raw prompts, audio and wearer data stay private."""
from datetime import datetime
import json
import math
import re
from .config import ROOT

FILES = {'grok_calls.jsonl', 'chalk_queries.jsonl', 'factory_runs.jsonl'}
ENUMS = {
    'result': {'CONFIRM', 'CANCEL', 'UNCLEAR', 'TIMEOUT', 'ERROR', 'SUPERSEDED', 'setup_failed', 'success', 'fallback', 'failed'},
    'phase': {'startup'},
    'source': {'browser microphone', 'generated speech fixture'},
    'test': {'generated_speech_no_arm', 'showcase_virtual_arm', 'factory_handoff'},
    'error': {'InvalidStatus', 'SSLCertVerificationError', 'ChalkBaseException', 'TimeoutError', 'RuntimeError', 'TimeoutExpired', 'ValueError', 'ConnectionError', 'OSError'},
}

def receipt(event):
    """Allowlist fields and values; never copy arbitrary response text."""
    out = {}
    try:
        stamp = datetime.fromisoformat(event['timestamp'])
        out['timestamp'] = stamp.isoformat()
    except (KeyError, TypeError, ValueError):
        pass
    for key in ('call_id', 'query_id', 'session_id'):
        value = event.get(key)
        if isinstance(value, str) and re.fullmatch(r'[a-fA-F0-9-]{16,64}', value):out[key] = value
    for key in ('latency_ms', 'decision_latency_ms'):
        value = event.get(key)
        if type(value) in (float, int) and math.isfinite(value) and value >= 0:out[key] = value
    for key in ('api_attempted', 'accepted', 'development'):
        if type(event.get(key)) is bool:out[key] = event[key]
    for key, allowed in ENUMS.items():
        value = event.get(key)
        if isinstance(value, str):out[key] = value if value in allowed else 'other'
    return out

def append_receipt(name, event):
    if name not in FILES:return
    folder = ROOT / 'evidence' / 'api_calls'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / name).open('a') as f:f.write(json.dumps(receipt(event), allow_nan=False) + '\n')

def export_history():
    """Rebuild tracked receipts from private logs while sponsor processes are stopped."""
    folder = ROOT / 'evidence' / 'api_calls'
    folder.mkdir(parents=True, exist_ok=True)
    for name in sorted(FILES):
        source = ROOT / 'logs' / name
        rows = []
        if source.exists():
            for line in source.read_text().splitlines():
                try:rows.append(receipt(json.loads(line)))
                except (ValueError, TypeError):continue
        target = folder / name
        staged = target.with_suffix('.tmp')
        staged.write_text(''.join(json.dumps(row, allow_nan=False) + '\n' for row in rows))
        staged.replace(target)

if __name__ == '__main__':export_history()
