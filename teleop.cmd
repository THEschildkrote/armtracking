@echo off
rem Leader (COM7) drives the follower (COM4). Ctrl+C to stop.
rem max_relative_target=20 caps each step at 20 degrees: stops sudden lurches but still lets the heavy elbow break away (3 was too small).
"%USERPROFILE%\miniconda3\envs\lerobot\Scripts\lerobot-teleoperate.exe" ^
  --robot.type=so101_follower --robot.port=COM4 --robot.id=my_follower ^
  --robot.max_relative_target=20 ^
  --teleop.type=so101_leader --teleop.port=COM7 --teleop.id=my_leader
