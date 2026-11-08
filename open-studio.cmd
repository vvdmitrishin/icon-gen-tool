@echo off
rem AI Collection Studio - one-click launcher (Windows, no admin rights, no registry changes).
rem   open-studio.cmd              set up what is missing, start the backend if needed, open the app
rem   open-studio.cmd /nobrowser   same, but do not open a browser tab
rem Quiet on success. On a failure a message box shows what went wrong (details: data\launcher.log).
rem The server keeps running in the background after this window closes. Stop it with stop-studio.cmd.
setlocal
cd /d "%~dp0"
set "URL=http://127.0.0.1:8000"
set "LOG=%~dp0data\launcher.log"
if not exist "data" mkdir "data" >nul 2>&1

powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri '%URL%/api/health' -UseBasicParsing -TimeoutSec 2).StatusCode | Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 goto open

echo ==== %date% %time% launcher start ==== >> "%LOG%"

rem ---- Python environment + dependencies (only when missing) ----
if not exist ".env" copy /y ".env.example" ".env" >nul 2>&1
if not exist "backend\.venv\Scripts\python.exe" (
  (py -3 -m venv backend\.venv || python -m venv backend\.venv) >> "%LOG%" 2>&1
  if errorlevel 1 call :fail "Python 3.11+ is required but could not be started. Install Python from python.org, then try again." & exit /b 1
  backend\.venv\Scripts\python -m pip install -q -e backend >> "%LOG%" 2>&1
  if errorlevel 1 call :fail "Installing the Python dependencies failed (no internet?)." & exit /b 1
)

rem ---- UI build: missing or older than the sources ----
powershell -NoProfile -Command "$d = Get-Item 'frontend\dist\index.html' -ErrorAction SilentlyContinue; if (-not $d) { exit 1 }; $n = Get-ChildItem 'frontend\src','frontend\index.html','frontend\package.json' -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if ($n -and $n.LastWriteTime -gt $d.LastWriteTime) { exit 1 }; exit 0"
if errorlevel 1 (
  if not exist "frontend\node_modules" (
    call npm --prefix frontend install --no-audit --no-fund >> "%LOG%" 2>&1
    if errorlevel 1 call :fail "Installing the UI dependencies failed. Node.js 18+ is required (nodejs.org)." & exit /b 1
  )
  call npm --prefix frontend run build >> "%LOG%" 2>&1
  if errorlevel 1 call :fail "Building the UI failed." & exit /b 1
)

powershell -NoProfile -Command "Start-Process -FilePath '%~dp0backend\.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','app.main:app_factory','--factory','--app-dir','backend','--host','127.0.0.1','--port','8000' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardError '%~dp0data\backend.err.log' -RedirectStandardOutput '%~dp0data\backend.out.log'"

set /a tries=0
:wait
set /a tries+=1
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri '%URL%/api/health' -UseBasicParsing -TimeoutSec 2).StatusCode | Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 goto open
if %tries% GEQ 60 ( call :fail "The server did not start within 60 seconds." & exit /b 1 )
timeout /t 1 /nobreak >nul
goto wait

:open
if /i "%~1"=="/nobrowser" exit /b 0
start "" "%URL%"
exit /b 0

:fail
echo [error] %~1 >> "%LOG%"
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('%~1' + [Environment]::NewLine + [Environment]::NewLine + 'Details: %LOG%', 'AI Collection Studio', 'OK', 'Error')"
exit /b 1
