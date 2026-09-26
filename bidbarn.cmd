@echo off
rem BidBarn, one click. Double-click this file and pick a mode.
rem   Learn mode: the bot's browser opens, you bid exactly as you normally do and type why in
rem               the blue box (Enter saves). The bot only watches. When you close the browser
rem               it scans the model codes you searched, compares its own proposals with what
rem               you did, and writes data\sessions\<stamp>\report.txt. Nothing is placed.
rem   Bid mode:   the bot signs you in if needed, fetches the bidding guides, scans Nichibo,
rem               opens the review page, and places the bids you approve after you click
rem               "Finish review" (dry run while the testing lock is on).
rem   First run:  creates the Python environment and installs the packages and the browser.
rem Needs Python 3.12 installed (https://www.python.org/downloads/, tick "Add to PATH").
rem Shortcuts:  bidbarn.cmd learn      bidbarn.cmd bid [--skip-scan] [--no-place] [--headed]
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

set "MODE=%~1"
if /i "%MODE%"=="learn" goto learn
if /i "%MODE%"=="bid" goto bid

echo BidBarn
echo.
echo   [L] Learn mode  You bid as usual in the bot's browser and type why in the blue box.
echo                   The bot only watches and learns. Nothing is placed.
echo   [B] Bid mode    The bot fetches the guides, scans Nichibo, opens the review page,
echo                   and places the bids you approve. Dry run while the testing lock is on.
echo.
choice /C LB /N /M "Press L for Learn or B for Bid: "
if errorlevel 2 goto bid
goto learn

:learn
"%PY%" -m auction_bot.cli learn
goto end

:bid
"%PY%" -m auction_bot.cli bid %2 %3 %4 %5
goto end

:end
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo BidBarn stopped with an error ^(exit code %RC%^). Read the message above.
pause
exit /b %RC%
