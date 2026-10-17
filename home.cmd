@echo off
rem Save (--save) or glide to the follower's rest pose. Works from any folder, no conda activate needed.
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m arm.home %*
