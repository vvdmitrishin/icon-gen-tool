@echo off
rem AI Collection Studio - local start (Windows).
rem   start.bat        build the UI once and serve everything from http://127.0.0.1:8000
rem   start.bat dev    backend (8000) + Vite dev server with hot reload (5173)
setlocal
cd /d "%~dp0"

if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo [setup] Created .env from .env.example. Add GEMINI_API_KEY to .env to use Gemini.
  echo [setup] Without a key you can still try the app with the free "Mock" provider.
)

if not exist "backend\.venv\Scripts\python.exe" (
  echo [setup] Creating Python virtual environment...
  py -3 -m venv backend\.venv 2>nul || python -m venv backend\.venv
  if errorlevel 1 ( echo [error] Python 3.11+ is required. & exit /b 1 )
)
echo [setup] Installing backend dependencies...
backend\.venv\Scripts\python -m pip install -q -e backend
if errorlevel 1 ( echo [error] pip install failed. & exit /b 1 )

if not exist "frontend\node_modules" (
  echo [setup] Installing frontend dependencies...
  call npm --prefix frontend install --no-audit --no-fund
  if errorlevel 1 ( echo [error] npm install failed. Node 18+ is required. & exit /b 1 )
)

if /i "%1"=="dev" (
  start "Collection Studio UI (dev)" cmd /c npm --prefix frontend run dev
  start "" http://127.0.0.1:5173
) else (
  echo [setup] Building the UI...
  call npm --prefix frontend run build
  if errorlevel 1 ( echo [error] UI build failed. & exit /b 1 )
  start "" http://127.0.0.1:8000
)

echo [run] Backend on http://127.0.0.1:8000  (Ctrl+C to stop)
backend\.venv\Scripts\python -m uvicorn app.main:app_factory --factory --app-dir backend --host 127.0.0.1 --port 8000
