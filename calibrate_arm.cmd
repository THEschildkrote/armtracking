@echo off
rem Recalibrate one arm. Works from any folder, no conda activate needed.
rem Usage: calibrate_arm.cmd follower     (COM4, id my_follower)
rem        calibrate_arm.cmd leader       (COM7, id my_leader)
rem Put the arm in the MIDDLE pose first: upper arm straight up, forearm level/forward,
rem wrist straight, wrist roll level, gripper half open. Use the SAME pose for both arms.
set EXE=%USERPROFILE%\miniconda3\envs\lerobot\Scripts\lerobot-calibrate.exe
if /i "%1"=="follower" "%EXE%" --robot.type=so101_follower --robot.port=COM4 --robot.id=my_follower & goto :eof
if /i "%1"=="leader" "%EXE%" --teleop.type=so101_leader --teleop.port=COM7 --teleop.id=my_leader & goto :eof
echo Usage: calibrate_arm.cmd follower ^| leader
