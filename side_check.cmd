@echo off
rem Live side-camera view for aiming and focusing: markers read, lock, sharpness. q to quit.
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m scene.side_check %*
