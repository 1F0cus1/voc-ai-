@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_scheduled_task.ps1"
if errorlevel 1 (
  echo.
  echo Failed to create the scheduled task. Try Run as administrator.
  pause
  exit /b 1
)
echo.
pause
