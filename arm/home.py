"""Save or go to the follower's rest ("home") pose.

The arm runner starts every pick from home and returns there afterwards, and
recordings should start from the same pose - so set it once, carefully.

Usage (from the armproject folder):
    home.cmd --save      # arm goes limp; place it in the rest pose by hand, press Enter
    home.cmd             # glide to the saved rest pose, then relax
    home.cmd --hold      # glide there and keep holding (torque stays on)
"""

import argparse
import json

from arm.motion import HOME_PATH, go_home, open_bus, read


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--save", action="store_true", help="Record the current pose as home")
    parser.add_argument("--hold", action="store_true", help="Keep torque on after arriving")
    args = parser.parse_args()

    if args.save:
        bus = open_bus(args.port)
        try:
            bus.disable_torque()
            input("Arm is limp. Put it in the rest pose (folded, resting on itself), then press Enter...")
            pose = read(bus)
        finally:
            bus.disconnect(disable_torque=True)
        HOME_PATH.write_text(json.dumps(pose, indent=2))
        print(f"Saved {HOME_PATH.name}: {pose}")
        return

    go_home(args.port, relax=not args.hold)
    print("At home pose" + (" (holding)" if args.hold else " (relaxed)"))


if __name__ == "__main__":
    main()
