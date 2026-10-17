@echo off
rem Set up / check the arm's front and wrist cameras. Works from any folder, no conda activate needed.
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m arm.camera_check %*
