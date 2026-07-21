@echo off
setlocal
cd /d "%~dp0"

where git.exe >nul 2>nul
if errorlevel 1 (
  echo Git is missing. Install Git for Windows first.
  pause
  exit /b 1
)

git diff --quiet
if errorlevel 1 (
  echo Tracked files have local changes. Update cancelled to protect them.
  pause
  exit /b 1
)
git diff --cached --quiet
if errorlevel 1 (
  echo Staged local changes exist. Update cancelled to protect them.
  pause
  exit /b 1
)

git pull --ff-only
if errorlevel 1 (
  echo Git update failed.
  pause
  exit /b 1
)

set "PYTHON_EXE=%~dp0.runtime\python\python.exe"
if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" -m py_compile "%~dp0scripts\voc_ai_tag_controller.py" "%~dp0scripts\voc_run_once.py"
  if errorlevel 1 (
    echo Updated code failed validation.
    pause
    exit /b 1
  )
)

echo Code update completed. Local configuration and runtime were preserved.
pause
