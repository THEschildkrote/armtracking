@echo off
rem Leader (COM7) drives the follower (COM4). Ctrl+C to stop.
rem max_relative_target=3 caps each step at 3 degrees so the follower glides instead of jumping.
"%USERPROFILE%\miniconda3\envs\lerobot\Scripts\lerobot-teleoperate.exe" ^
  --robot.type=so101_follower --robot.port=COM4 --robot.id=my_follower ^
  --robot.max_relative_target=3 ^
  --teleop.type=so101_leader --teleop.port=COM7 --teleop.id=my_leader
