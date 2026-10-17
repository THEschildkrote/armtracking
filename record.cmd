@echo off
rem Record demonstrations with the leader arm. Works from any folder, no conda activate needed.
rem Usage: record.cmd pick_red "Pick up the red block and put it in the bowl" [--episodes 50] [--resume]
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m arm.record %*
