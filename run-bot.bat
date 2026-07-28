@echo off
REM ============================================================
REM  Sniper MAYHEM bot - auto-restart launcher (Windows)
REM  Runs bot_runner.py and relaunches it if it ever crashes.
REM  The .bot.pid single-owner lock prevents a second instance.
REM ============================================================
cd /d "%~dp0"
set PYTHONPATH=src
title Sniper Mayhem Bot (auto-restart)

if not exist ".venv\Scripts\python.exe" (
  echo No .venv found. Run first:  python -m uv sync
  pause
  exit /b 1
)

:loop
echo [%date% %time%] starting mayhem bot ...
".venv\Scripts\python.exe" -u src\bot_runner.py
echo [%date% %time%] bot stopped (exit %errorlevel%). Restarting in 5s... Ctrl+C to quit.
timeout /t 5 /nobreak >nul
goto loop
