@echo off
cd /d "%~dp0"
echo Before running: create a NAMED Cloudflare Tunnel, route its hostname to http://127.0.0.1:8765,
echo and protect that hostname with Cloudflare Access (allow only your email).
echo The hostname MUST NOT be accessible without authentication.
set /p TUNNEL_NAME=Named tunnel (configured on this PC): 
if "%TUNNEL_NAME%"=="" exit /b 1
start "Comfy Mobile Monitor" "%~dp0start_monitor.bat"
timeout /t 3 >nul
cloudflared tunnel run "%TUNNEL_NAME%"
pause
