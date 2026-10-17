"""Read-only health check for an SO-101 arm.

Checks that all 6 servos answer on the bus, turns torque OFF (arm goes limp),
then prints raw joint positions so you can move each joint by hand and watch
the numbers change. It never commands the arm to move.

Usage (inside `conda activate lerobot`):
    python check_arm.py COM4            # follower or leader on COM4
    python check_arm.py COM4 --seconds 30
"""

import argparse
import time

from lerobot.motors import Motor, MotorNormMode, SerialMotorsBus

MOTORS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", help="Serial port, e.g. COM4")
    parser.add_argument("--seconds", type=float, default=15, help="How long to print positions")
    args = parser.parse_args()

    bus = SerialMotorsBus(
        port=args.port,
        motors={name: Motor(i, "sts3215", MotorNormMode.RANGE_M100_100) for i, name in enumerate(MOTORS, 1)},
    )

    print(f"Connecting on {args.port}...")
    bus.connect(handshake=False)
    try:
        missing = []
        for name in MOTORS:
            model = bus.ping(name, num_retry=2)
            print(f"  ID {bus.motors[name].id} {name:<14} {'OK' if model is not None else 'NO RESPONSE'}")
            if model is None:
                missing.append(name)
        if missing:
            print(f"\nNot responding: {', '.join(missing)}. Check power, cables and motor IDs.")
            return

        bus.disable_torque()
        print(f"\nAll 6 servos found. Torque off - move each joint by hand for {args.seconds:.0f}s.")
        print("Each number should change smoothly (raw range 0-4095). Ctrl+C to stop.\n")
        print("  ".join(f"{n[:11]:>11}" for n in MOTORS))
        end = time.time() + args.seconds
        while time.time() < end:
            pos = bus.sync_read("Present_Position", normalize=False)
            print("  ".join(f"{pos[n]:>11}" for n in MOTORS), end="\r")
            time.sleep(0.1)
        print()
    except KeyboardInterrupt:
        print()
    finally:
        bus.disconnect(disable_torque=False)


if __name__ == "__main__":
    main()
