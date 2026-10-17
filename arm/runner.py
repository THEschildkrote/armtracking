"""Arm runner: a gaze selection becomes an arm action.

For each object name, arm/policies.json says what to run:
    {
      "red":  {"type": "policy", "path": "outputs/train/pick_red/checkpoints/last/pretrained_model",
               "task": "Pick up the red block and put it in the bowl", "duration": 20},
      "blue": {"type": "scripted"}
    }
Sequence for one selection, in a background thread so the gaze view keeps running:
    home (hold)  ->  policy rollout or scripted pick  ->  home (relax)  ->  idle
Further selections are ignored while the arm is busy.

Test from the command line (from the armproject folder):
    python -m arm.runner red
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from arm import cameras
from arm.motion import go_home

POLICIES_PATH = Path(__file__).with_name("policies.json")
ENV = Path(os.environ["USERPROFILE"]) / "miniconda3" / "envs" / "lerobot"
ROOT = Path(__file__).resolve().parent.parent


class ArmRunner:
    def __init__(self, port: str = "COM4", fps: int = 30):
        self.port = port
        self.fps = fps
        self.status = "idle"
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def request(self, name: str) -> bool:
        """Start picking `name`. Returns False (and does nothing) if the arm is busy."""
        if self.busy:
            return False
        self._thread = threading.Thread(target=self._run, args=(name,), daemon=True)
        self._thread.start()
        return True

    def _run(self, name: str) -> None:
        try:
            specs = json.loads(POLICIES_PATH.read_text())
            if name not in specs:
                raise RuntimeError(f"{name!r} is not in arm/policies.json")
            spec = specs[name]
            self.status = "homing"
            go_home(self.port, relax=False)
            self.status = f"picking {name}"
            if spec["type"] == "policy":
                self._rollout(spec)
            elif spec["type"] == "scripted":
                from arm.scripted import pick  # needs ikpy + the SO-101 model; only loaded when used

                pick(name, self.port)
            else:
                raise RuntimeError(f"unknown type {spec['type']!r} for {name}")
            self.status = "returning"
            go_home(self.port, relax=True)
            self.status = "idle"
        except Exception as e:  # keep the gaze view alive; show what went wrong
            self.status = f"error: {e}"
            print(f"ARM ERROR ({name}): {e}", file=sys.stderr, flush=True)

    def _rollout(self, spec: dict) -> None:
        policy = (ROOT / spec["path"]).resolve()
        if not policy.exists():
            raise RuntimeError(f"no checkpoint at {policy}")
        cmd = [
            str(ENV / "Scripts" / "lerobot-rollout.exe"),
            "--strategy.type=base",
            f"--policy.path={policy}",
            "--robot.type=so101_follower",
            f"--robot.port={self.port}",
            "--robot.id=my_follower",
            "--robot.disable_torque_on_disconnect=false",  # hold the last pose; we glide home ourselves
            f"--robot.cameras={cameras.lerobot_arg(self.fps)}",
            f"--task={spec.get('task', '')}",
            f"--duration={spec.get('duration', 20)}",
        ]
        result = subprocess.run(cmd, cwd=ROOT, stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            raise RuntimeError(f"policy rollout exited with code {result.returncode}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m arm.runner <object name>")
    runner = ArmRunner()
    runner.request(sys.argv[1])
    runner._thread.join()
    print("final status:", runner.status)
