@echo off
rem Live pupil viewer. Works from any folder, no conda activate needed.
rem Usage: eye.cmd --camera 3     (any eye.live options pass through)
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m eye.live %*
