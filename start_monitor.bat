@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 comfy_monitor.py) else (python comfy_monitor.py)
pause
