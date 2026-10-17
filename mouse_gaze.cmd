@echo off
rem Mouse as gaze: test dwell -> pre-hover -> long blink (b) -> pick without the glasses. s = STOP, q = quit.
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\lerobot\python.exe" -m gaze.mouse_gaze %*
