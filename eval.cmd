@echo off
rem Test a trained policy on the arm and log the success rate. No conda activate needed.
rem Usage: eval.cmd pick_red [--trials 10] [--checkpoint last]
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m arm.evaluate %*
