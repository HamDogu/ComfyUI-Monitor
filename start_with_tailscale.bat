@echo off
cd /d "%~dp0"
echo Launching Comfy Mobile Monitor ...
start "Comfy Mobile Monitor" "%~dp0start_monitor.bat"
timeout /t 3 >nul
echo Starting PRIVATE Tailscale Serve. Your Windows PC and phone must both sign into Tailscale.
set "TS=tailscale"
where tailscale >nul 2>nul
if errorlevel 1 (
  if exist "%ProgramFiles%\Tailscale\tailscale.exe" (
    set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
  ) else (
    echo Tailscale CLI not found. Install Tailscale, then run: tailscale serve --bg 8765
    pause
    exit /b 1
  )
)
"%TS%" serve --bg 8765
"%TS%" serve status
echo Only devices on your Tailscale network can access this address.
pause
