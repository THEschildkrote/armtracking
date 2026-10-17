"""Record teleoperated demonstrations for training (wraps lerobot-record).

One dataset per task: ACT has no language input, so each object gets its own
dataset and policy (e.g. pick_red, pick_blue). Datasets stay local in
~/.cache/huggingface/lerobot/local/<task>.

Usage (from the armproject folder):
    record.cmd pick_red "Pick up the red block and put it in the bowl"
    record.cmd pick_red "..." --episodes 10 --resume      # add 10 more to an existing dataset
    record.cmd pick_red "..." --fps 30

During recording (keys in the terminal window):
    Right arrow   end this episode early and move on
    Left arrow    re-record this episode
    Esc           stop and save everything recorded so far
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from arm import cameras

ENV = Path(os.environ["USERPROFILE"]) / "miniconda3" / "envs" / "lerobot"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="Dataset name, e.g. pick_red")
    parser.add_argument("task", help="Sentence describing the task (stored with every episode)")
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--episode-time", type=float, default=20, help="Seconds per episode")
    parser.add_argument("--reset-time", type=float, default=10, help="Seconds to reset the scene between episodes")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--follower", default="COM4")
    parser.add_argument("--leader", default="COM7")
    parser.add_argument("--resume", action="store_true", help="Append episodes to an existing dataset")
    args = parser.parse_args()

    cmd = [
        str(ENV / "Scripts" / "lerobot-record.exe"),
        "--robot.type=so101_follower",
        f"--robot.port={args.follower}",
        "--robot.id=my_follower",
        f"--robot.cameras={cameras.lerobot_arg(args.fps)}",
        "--teleop.type=so101_leader",
        f"--teleop.port={args.leader}",
        "--teleop.id=my_leader",
        f"--dataset.repo_id=local/{args.name}",
        f"--dataset.single_task={args.task}",
        f"--dataset.num_episodes={args.episodes}",
        f"--dataset.episode_time_s={args.episode_time}",
        f"--dataset.reset_time_s={args.reset_time}",
        f"--dataset.fps={args.fps}",
        "--dataset.push_to_hub=false",
    ]
    if args.resume:
        cmd.append("--resume=true")
    print(" ".join(cmd), "\n")
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
