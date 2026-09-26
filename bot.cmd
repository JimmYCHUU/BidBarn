@echo off
rem Launcher for BidBarn, Carbarn's Nichibo auction bidding assistant.
rem The generated .venv\Scripts\bot.exe is blocked by Windows App Control on this PC,
rem so this file runs the CLI through the venv's python.exe instead.
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

if "%~1"=="" (
  echo BidBarn - Carbarn's Nichibo auction bidding assistant
  echo.
  echo Double-click one of these files instead of bot.cmd:
  echo   bidbarn.cmd  the whole day in one go: sign in, guides, scan, review, place
  echo   login.cmd    sign in to Nichibo and the Carbarn dashboard once
  echo   scan.cmd     fetch the bidding guides and scan Nichibo
  echo   review.cmd   open the review page in your browser
  echo   place.cmd    place the approved bids ^(dry run unless config says otherwise^)
  echo.
  echo Or open a terminal in this folder and run, for example:
  echo   .\bot.cmd scan -m GDH206 --limit 5
  echo.
  "%PY%" -m auction_bot.cli --help
  echo.
  pause
  exit /b 0
)

"%PY%" -m auction_bot.cli %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo The command ended with an error ^(exit code %RC%^). Read the message above.
  echo %CMDCMDLINE% | find /i "/c" >nul && pause
)
exit /b %RC%
