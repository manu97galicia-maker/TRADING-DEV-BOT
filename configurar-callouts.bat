@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_callout_publisher.py --setup
if errorlevel 1 goto fin
".venv\Scripts\python.exe" run_callout_publisher.py --run
:fin
pause
