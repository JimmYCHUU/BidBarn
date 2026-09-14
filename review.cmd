@echo off
rem Starts the local review page and opens it in your browser. Close this window to stop it.
cd /d "%~dp0"
start "" cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8765/"
"%~dp0.venv\Scripts\python.exe" -m auction_bot.cli review
pause
