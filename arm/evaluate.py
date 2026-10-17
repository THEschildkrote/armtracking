"""Test a trained policy on the real arm and keep score (wraps lerobot-rollout).

Each trial: you set the object somewhere new, press Enter, the policy runs for
--duration seconds, then you type y/n for success. Results are appended to
outputs/eval/<name>.csv so you can compare checkpoints.

Usage (from the armproject folder):
    eval.cmd pick_red                         # 10 trials, last checkpoint
    eval.cmd pick_red --trials 5 --checkpoint 020000
"""

import argparse
import csv
import os
import subprocess
import time
from pathlib import Path

from arm import cameras

ENV = Path(os.environ["USERPROFILE"]) / "miniconda3" / "envs" / "lerobot"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="Training run name, e.g. pick_red")
    parser.add_argument("--checkpoint", default="last", help="Checkpoint folder name, e.g. last or 020000")
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--duration", type=float, default=20, help="Seconds per trial")
    parser.add_argument("--task", default="", help="Task sentence (defaults to the run name)")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--follower", default="COM4")
    args = parser.parse_args()

    policy = Path("outputs") / "train" / args.name / "checkpoints" / args.checkpoint / "pretrained_model"
    if not policy.exists():
        raise SystemExit(f"No checkpoint at {policy}")
    cmd = [
        str(ENV / "Scripts" / "lerobot-rollout.exe"),
        "--strategy.type=base",
        f"--policy.path={policy}",
        "--robot.type=so101_follower",
        f"--robot.port={args.follower}",
        "--robot.id=my_follower",
        f"--robot.cameras={cameras.lerobot_arg(args.fps)}",
        f"--task={args.task or args.name}",
        f"--duration={args.duration}",
    ]
    out = Path("outputs") / "eval" / f"{args.name}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()
    successes = 0
    with out.open("a", newline="") as f:
        log = csv.writer(f)
        if new_file:
            log.writerow(["time", "checkpoint", "trial", "success", "note"])
        for trial in range(1, args.trials + 1):
            input(f"\nTrial {trial}/{args.trials}: place the object, keep hands clear, press Enter to run...")
            subprocess.call(cmd)
            answer = input("Success? [y/n] (optionally add a note after a space): ").strip()
            ok = answer[:1].lower() == "y"
            successes += ok
            log.writerow([time.strftime("%Y-%m-%d %H:%M"), args.checkpoint, trial, int(ok), answer[2:]])
            f.flush()
    print(f"\n{args.name} @ {args.checkpoint}: {successes}/{args.trials} successful -> logged in {out}")


if __name__ == "__main__":
    main()
