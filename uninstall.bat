@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ScheduledTask -TaskPath \Timescar\ -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false; Write-Host done"
pause
