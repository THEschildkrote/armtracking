@echo off
rem Gaze calibration with on-screen targets. Works from any folder, no conda activate needed.
rem Usage: calib.cmd [--eye 3] [--scene http://172.20.10.2/]
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m gaze.calibrate %*
