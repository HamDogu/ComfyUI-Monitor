@echo off
cd /d "%~dp0"
echo CAUTION: Protected Cloudflare Quick Tunnel login may repeatedly expire on some versions.
echo Use Tailscale for the recommended stable private connection.
set /p ALLOWED_EMAIL=Email to allow: 
if "%ALLOWED_EMAIL%"=="" exit /b 1
start "Comfy Mobile Monitor" "%~dp0start_monitor.bat"
timeout /t 3 >nul
cloudflared tunnel --url http://127.0.0.1:8765 --allowed-mail "%ALLOWED_EMAIL%"
pause
