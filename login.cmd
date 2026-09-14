@echo off
rem One-time sign-in. A browser window opens with two tabs: Nichibo and the Carbarn dashboard.
rem Sign in to BOTH, then come back to this window and press Enter.
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m auction_bot.cli login
echo.
pause
