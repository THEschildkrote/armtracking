@echo off
rem Camera relay: serve side / wrist / eye / front cameras to other machines. No conda activate needed.
rem Usage: relay.cmd [--only side wrist] [--fps 15] [--quality 75]
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m streams.relay %*
