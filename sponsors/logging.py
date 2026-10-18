import csv
import json
import threading
from datetime import datetime, timezone
from .config import ROOT

_lock = threading.RLock()

def timestamp():
    return datetime.now(timezone.utc).isoformat()

def log(name, **event):
    """Telemetry must never prevent blink or arm control; never record secrets/audio."""
    try:
        with _lock:
            path = ROOT / 'logs' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a') as f:
                record = {'timestamp': timestamp(), **event}
                f.write(json.dumps(record, allow_nan=False)+'\n')
            from .call_evidence import append_receipt
            append_receipt(name, record)
    except (OSError, ValueError):
        pass

def grab_event(user, label, status, source):
    try:
        with _lock:
            path = ROOT / 'logs' / 'grab_events.csv'
            path.parent.mkdir(parents=True, exist_ok=True)
            new = not path.exists() or path.stat().st_size == 0
            with path.open('a', newline='') as f:
                w=csv.writer(f)
                if new:w.writerow(['timestamp','user_id','object_label','status','source','synthetic'])
                w.writerow([timestamp(),user,label,status,source,'false'])
    except OSError:
        pass
