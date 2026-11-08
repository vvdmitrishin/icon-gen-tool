@echo off
rem Stops the background AI Collection Studio backend started by open-studio.cmd (port 8000 only).
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
echo Stopped (if it was running).
