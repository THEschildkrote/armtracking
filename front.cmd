@echo off
rem Live front-camera view with table markers. Works from any folder, no conda activate needed.
rem Usage: front.cmd --camera http://172.20.10.2/     (or a camera number)
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m scene.live %*
