@echo off
setlocal
schtasks.exe /Delete /TN "VOC AI Tagger" /F
if errorlevel 1 echo Scheduled task was not found or could not be deleted.
pause
