@echo off
setlocal
cd /d "%~dp0"
call "%~dp0start_desktop.bat" %*
exit /b %errorlevel%
