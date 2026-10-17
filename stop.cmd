@echo off
rem STOP: freezes the follower arm where it is (torque stays on). Works while any pick, hover or homing runs.
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -c "from arm.motion import request_stop; request_stop(); print('STOP sent')"
