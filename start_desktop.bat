@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0.runtime\python\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Portable runtime is missing. Starting first-time setup...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_new_pc.ps1"
  if errorlevel 1 (
    echo.
    echo Setup failed. Check the message above, then run setup_new_pc.bat again.
    pause
    exit /b 1
  )
)

"%PYTHON_EXE%" -c "import pymysql, tkinter" >nul 2>nul
if errorlevel 1 (
  echo Runtime verification failed. Run setup_new_pc.bat again.
  pause
  exit /b 1
)

"%PYTHON_EXE%" "%~dp0scripts\voc_ai_tag_controller.py"
if errorlevel 1 pause
