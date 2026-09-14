@echo off
rem Places every bid you approved on the review page.
rem Dry run by default: the amount is typed on Nichibo and screenshotted, but Place Bid is NOT clicked.
rem Live placement needs BOTH  dry_run: false  in config.yaml  AND  the --live flag:
rem     .\bot.cmd place --live
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m auction_bot.cli place
echo.
pause
