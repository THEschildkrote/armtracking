@echo off
rem Add the big head-camera markers (ids 4-7) to scene\config.json via the locked side camera.
cd /d "%~dp0"
"%USERPROFILE%\miniconda3nvs\lerobot\python.exe" -m scene.add_big_markers
