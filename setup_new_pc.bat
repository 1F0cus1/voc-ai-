@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_new_pc.ps1"
if errorlevel 1 (
  echo.
  echo Setup failed. See the message above.
  pause
  exit /b 1
)

call "%~dp0start_desktop.bat"
