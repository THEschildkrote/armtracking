"""Pick success so far, from logs/picks.jsonl (every scripted grasp attempt appends a line).

    python -m arm.pick_stats            (or: pick_stats.cmd)

"held" is the gripper's own verdict - it stops short of fully closed when something is between the
jaws - so a pick that grabbed the object and dropped it on the way still counts as held.
"""

import json
from collections import defaultdict
from pathlib import Path

LOG = Path(__file__).resolve().parents[1] / "logs" / "picks.jsonl"


def main() -> None:
    if not LOG.exists():
        print("No picks logged yet.")
        return
    rows = [json.loads(line) for line in LOG.read_text().splitlines() if line.strip()]
    by = defaultdict(list)
    for r in rows:
        by[r.get("cls") or r["name"]].append(r)
    print(f"{'object':16} {'held':>9}  {'low approach':>12}")
    for cls, rs in sorted(by.items()):
        held = sum(r["held"] for r in rs)
        low = [r for r in rs if r.get("low_approach")]
        print(f"{cls:16} {held:3}/{len(rs):<3} {100 * held / len(rs):3.0f}%  "
              + (f"{sum(r['held'] for r in low)}/{len(low)}" if low else "-").rjust(12))
    total = sum(r["held"] for r in rows)
    print(f"{'all':16} {total:3}/{len(rows):<3} {100 * total / len(rows):3.0f}%")


if __name__ == "__main__":
    main()
