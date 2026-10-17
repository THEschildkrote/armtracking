@echo off
rem Scripted pick fallback. Works from any folder, no conda activate needed.
rem Usage: scripted.cmd calibrate      (touch the gripper tip to each table marker, then the drop spot)
rem        scripted.cmd pick granola   (pick an object from gaze\objects.json; drop spot or hand-over)
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m arm.scripted %*
