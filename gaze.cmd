@echo off
rem Live gaze on the front camera with blink commands. Works from any folder, no conda activate needed.
rem Usage: gaze.cmd [--eye 3] [--scene http://172.20.10.2/]
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m gaze.live %*
