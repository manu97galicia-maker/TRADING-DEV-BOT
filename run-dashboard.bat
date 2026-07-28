@echo off
REM ============================================================
REM  Sniper Bot - Control Deck (dashboard) launcher for Windows
REM  Uses the project's .venv directly (created by `uv sync`),
REM  so it works without uv on PATH. Auto-restarts on crash.
REM  Open http://127.0.0.1:8787  (local only)
REM ============================================================
cd /d "%~dp0"
set PYTHONPATH=src
title Sniper Bot Dashboard

if not exist ".venv\Scripts\python.exe" (
  echo No .venv found. Run first:  python -m uv sync
  pause
  exit /b 1
)

:loop
echo [%date% %time%] starting dashboard on http://127.0.0.1:8787 ...
".venv\Scripts\python.exe" -m dashboard
echo [%date% %time%] dashboard stopped (exit %errorlevel%). Restarting in 5s... Ctrl+C to quit.
timeout /t 5 /nobreak >nul
goto loop
