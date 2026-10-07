@echo off
setlocal

title NTU Invoice Agent - Supervisor
echo ============================================================
echo   NTU Invoice Agent - Windows 11 one-click launcher
echo ============================================================
echo.

rem The batch file is intended to sit on the Windows Desktop beside a project
rem folder named NTU_invoice_agent.
set "PROJECT_DIR="

if defined NTU_INVOICE_AGENT_DIR (
  if exist "%NTU_INVOICE_AGENT_DIR%\win11_supervisor.ps1" (
    set "PROJECT_DIR=%NTU_INVOICE_AGENT_DIR%"
  )
)

if not defined PROJECT_DIR (
  if exist "%~dp0win11_supervisor.ps1" set "PROJECT_DIR=%~dp0"
)

if not defined PROJECT_DIR (
  if exist "%~dp0NTU_invoice_agent\win11_supervisor.ps1" (
    set "PROJECT_DIR=%~dp0NTU_invoice_agent"
  )
)

if not defined PROJECT_DIR (
  echo ERROR: Cannot find the NTU_invoice_agent project folder.
  echo.
  echo Put the project here:
  echo   %%USERPROFILE%%\Desktop\NTU_invoice_agent
  echo.
  echo Or set NTU_INVOICE_AGENT_DIR to the full Win11 project path.
  echo Example:
  echo   setx NTU_INVOICE_AGENT_DIR "D:\path\to\NTU_invoice_agent"
  echo.
  pause
  exit /b 1
)

for %%I in ("%PROJECT_DIR%") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%"

echo Project folder: %PROJECT_DIR%
echo.
echo This window supervises WSL servers and Electron.
echo Keep it open. Press Ctrl+C to stop the supervisor.
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass ^
  -File "%PROJECT_DIR%\win11_supervisor.ps1" ^
  -ProjectRoot "%PROJECT_DIR%"

if errorlevel 1 (
  echo.
  echo Startup failed. Read the error above, then press any key.
  pause >nul
)

endlocal
