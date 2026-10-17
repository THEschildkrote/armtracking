@echo off
rem Train an ACT policy on a recorded dataset. Works from any folder, no conda activate needed.
rem Usage: train.cmd pick_red [--steps 50000] [--resume]
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m arm.train %*
