@echo off
rem Live object detection on the side camera -> gaze\objects.json. q to quit. Close side_check first.
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m scene.live_objects %*
