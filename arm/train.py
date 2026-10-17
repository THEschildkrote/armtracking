"""Train an ACT policy on one recorded dataset (wraps lerobot-train).

Sized for the 4 GB RTX A2000 laptop GPU: batch 8 fits ACT with two 640x480
cameras. Checkpoints land in outputs/train/<name>/checkpoints/.

Usage (from the armproject folder):
    train.cmd pick_red                    # 50k steps
    train.cmd pick_red --steps 20000      # quicker first try
    train.cmd pick_red --resume           # continue after a stop or crash
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ENV = Path(os.environ["USERPROFILE"]) / "miniconda3" / "envs" / "lerobot"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="Dataset name used with record.cmd, e.g. pick_red")
    parser.add_argument("--steps", type=int, default=50_000)
    parser.add_argument("--batch-size", type=int, default=8, help="Lower to 4 if you run out of GPU memory")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    out = Path("outputs") / "train" / args.name
    if args.resume:
        cmd = [
            str(ENV / "Scripts" / "lerobot-train.exe"),
            f"--config_path={out / 'checkpoints' / 'last' / 'pretrained_model' / 'train_config.json'}",
            "--resume=true",
        ]
    else:
        cmd = [
            str(ENV / "Scripts" / "lerobot-train.exe"),
            f"--dataset.repo_id=local/{args.name}",
            "--policy.type=act",
            "--policy.device=cuda",
            "--policy.push_to_hub=false",
            f"--output_dir={out}",
            f"--job_name={args.name}",
            f"--batch_size={args.batch_size}",
            f"--steps={args.steps}",
            "--save_freq=10000",
            "--num_workers=2",
            "--wandb.enable=false",
        ]
    print(" ".join(cmd), "\n")
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
