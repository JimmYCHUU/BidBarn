@echo off
rem BidBarn dashboard. Opens the page in your browser. The server runs in the minimised
rem "BidBarn dashboard" window in the taskbar; close that window to stop it.
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo The Python environment is missing. Run bidbarn.cmd once first.
  pause
  exit /b 1
)
start "BidBarn dashboard" /min "%~dp0.venv\Scripts\python.exe" -m auction_bot.cli dashboard
