@echo off
rem Pick success so far (logs\picks.jsonl).
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m arm.pick_stats
