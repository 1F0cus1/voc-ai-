@echo off
setlocal
cd /d "%~dp0"
call "%~dp0install_schedule.bat"
exit /b %errorlevel%
