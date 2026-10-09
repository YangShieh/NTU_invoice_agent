@echo off
setlocal

title NTU Invoice Agent - Supervisor
echo ============================================================
echo   NTU Invoice Agent - Windows 11 one-click launcher
echo ============================================================
echo.

rem Resolve the project folder in this order:
rem   1. NTU_INVOICE_AGENT_DIR environment variable
rem   2. The folder containing this BAT file
rem   3. NTU_invoice_agent beside this BAT file
rem   4. %%USERPROFILE%%\Desktop\NTU_invoice_agent
set "PROJECT_DIR="

if defined NTU_INVOICE_AGENT_DIR (
  if exist "%NTU_INVOICE_AGENT_DIR%\win11_supervisor.ps1" (
    if exist "%NTU_INVOICE_AGENT_DIR%\backend\" (
      if exist "%NTU_INVOICE_AGENT_DIR%\frontend\" (
        if exist "%NTU_INVOICE_AGENT_DIR%\electron\" (
          set "PROJECT_DIR=%NTU_INVOICE_AGENT_DIR%"
        )
      )
    )
  )
)

if not defined PROJECT_DIR (
  if exist "%~dp0win11_supervisor.ps1" (
    if exist "%~dp0backend\" (
      if exist "%~dp0frontend\" (
        if exist "%~dp0electron\" (
          set "PROJECT_DIR=%~dp0"
        )
      )
    )
  )
)

if not defined PROJECT_DIR (
  if exist "%~dp0NTU_invoice_agent\win11_supervisor.ps1" (
    if exist "%~dp0NTU_invoice_agent\backend\" (
      if exist "%~dp0NTU_invoice_agent\frontend\" (
        if exist "%~dp0NTU_invoice_agent\electron\" (
          set "PROJECT_DIR=%~dp0NTU_invoice_agent"
        )
      )
    )
  )
)

if not defined PROJECT_DIR (
  if exist "%USERPROFILE%\Desktop\NTU_invoice_agent\win11_supervisor.ps1" (
    if exist "%USERPROFILE%\Desktop\NTU_invoice_agent\backend\" (
      if exist "%USERPROFILE%\Desktop\NTU_invoice_agent\frontend\" (
        if exist "%USERPROFILE%\Desktop\NTU_invoice_agent\electron\" (
          set "PROJECT_DIR=%USERPROFILE%\Desktop\NTU_invoice_agent"
        )
      )
    )
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
