@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PYTHON_EXE%" (
  set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" -c "import pymysql" >nul 2>nul
if errorlevel 1 (
  echo Installing dependency: pymysql
  "%PYTHON_EXE%" -m pip install pymysql
  if errorlevel 1 (
    echo.
    echo Failed to install pymysql.
    echo Please run this command manually:
    echo "%PYTHON_EXE%" -m pip install pymysql
    pause
    exit /b 1
  )
)

"%PYTHON_EXE%" scripts\voc_ai_tag_controller.py
pause
