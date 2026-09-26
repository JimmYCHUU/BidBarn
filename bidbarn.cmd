@echo off
rem BidBarn, one click. Double-click this file and nothing else.
rem   First run:  creates the Python environment, installs the packages and the browser.
rem   Every run:  signs you in if the session expired, fetches the bidding guides, scans
rem               Nichibo, opens the review page, and places the approved bids after you
rem               click "Finish review" on that page (dry run while the testing lock is on).
rem Needs Python 3.12 installed (https://www.python.org/downloads/, tick "Add to PATH").
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo First run: setting BidBarn up. This takes a few minutes and happens once.
  echo.
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python was not found. Install Python 3.12 from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" in the installer, then double-click this file again.
    pause
    exit /b 1
  )
  python -m venv .venv
  if errorlevel 1 ( echo Could not create the Python environment. & pause & exit /b 1 )
  "%PY%" -m pip install --quiet --upgrade pip
  "%PY%" -m pip install --quiet -e .
  if errorlevel 1 ( echo Could not install the packages. Check the internet connection and try again. & pause & exit /b 1 )
  "%PY%" -m playwright install chromium
  if errorlevel 1 ( echo Could not install the browser. Check the internet connection and try again. & pause & exit /b 1 )
  echo.
  echo Setup done.
  echo.
)

"%PY%" -m auction_bot.cli run %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo BidBarn stopped with an error ^(exit code %RC%^). Read the message above.
pause
exit /b %RC%
