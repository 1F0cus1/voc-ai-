@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -Command "$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }"
if errorlevel 1 (
  set "VOC_SCHEDULE_INSTALLER=%~f0"
  powershell.exe -NoProfile -Command "Start-Process -FilePath $env:VOC_SCHEDULE_INSTALLER -Verb RunAs"
  if errorlevel 1 (
    echo Administrator approval was cancelled. The scheduled task was not changed.
    pause
    exit /b 1
  )
  exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_scheduled_task.ps1"
if errorlevel 1 (
  echo.
  echo Failed to create the scheduled task. Try Run as administrator.
  pause
  exit /b 1
)
echo.
pause
