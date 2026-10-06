@echo off
setlocal
cd /d "%~dp0"

title NTU Invoice Agent - Supervisor
echo ============================================================
echo   NTU Invoice Agent - Windows 11 one-click launcher
echo ============================================================
echo.
echo This window supervises WSL servers and Electron.
echo Keep it open. Press Ctrl+C to stop the supervisor.
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass ^
  -File "%~dp0win11_supervisor.ps1"

if errorlevel 1 (
  echo.
  echo Startup failed. Read the error above, then press any key.
  pause >nul
)

endlocal
