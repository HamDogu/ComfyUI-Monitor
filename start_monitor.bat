@echo off
cd /d "%~dp0"
rem The app's Restart button exits with code 3; loop to start it again with the latest code.
set COMFY_MONITOR_LAUNCHER=1
:run
where py >nul 2>nul
if %errorlevel%==0 (py -3 comfy_monitor.py) else (python comfy_monitor.py)
if %errorlevel%==3 goto run
pause
