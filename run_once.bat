@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0.runtime\python\python.exe"

if not exist "%PYTHON_EXE%" exit /b 10
"%PYTHON_EXE%" "%~dp0scripts\voc_run_once.py" %*
exit /b %errorlevel%
