@echo off
rem Daily scan: refresh the bidding guides from Carbarn, then search Nichibo for every active guide.
rem To scan one model only, open a terminal here and run:  .\bot.cmd scan -m GDH206 --limit 5
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
"%PY%" -m auction_bot.cli fetch-rules
if errorlevel 1 (
  echo.
  echo Could not fetch the bidding guides. If it says "not logged in", double-click login.cmd first.
  pause
  exit /b 1
)
"%PY%" -m auction_bot.cli scan
echo.
echo Scan finished. Double-click review.cmd to review the candidates.
pause
