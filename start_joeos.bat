@echo off
setlocal
cd /d "%~dp0"

set "JOEOS_VENV=%JOEOS_VENV_DIR%"
if not defined JOEOS_VENV set "JOEOS_VENV=%CD%\.venv"

if not exist "%JOEOS_VENV%\Scripts\python.exe" (
  echo Creating the private JoeOS Python environment...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv "%JOEOS_VENV%"
  ) else (
    where python >nul 2>nul
    if errorlevel 1 (
      echo Python 3 is required. Install it, then run this launcher again.
      pause
      exit /b 1
    )
    python -m venv "%JOEOS_VENV%"
  )
)

"%JOEOS_VENV%\Scripts\python.exe" -c "import fastapi, httpx, psutil, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo Installing JoeOS runtime packages ^(first launch only^)...
  "%JOEOS_VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo JoeOS package installation failed.
    pause
    exit /b 1
  )
)

set "JOEOS_BIND_HOST=%JOEOS_HOST%"
if not defined JOEOS_BIND_HOST (
  for /f "usebackq delims=" %%I in (`tailscale ip -4 2^>nul`) do if not defined JOEOS_BIND_HOST set "JOEOS_BIND_HOST=%%I"
)
if not defined JOEOS_BIND_HOST set "JOEOS_BIND_HOST=127.0.0.1"
set "JOEOS_BIND_PORT=%JOEOS_PORT%"
if not defined JOEOS_BIND_PORT set "JOEOS_BIND_PORT=8080"
if not defined LEMONADE_BASE_URL set "LEMONADE_BASE_URL=http://127.0.0.1:13305/api/v1"
set "PYTHONUNBUFFERED=1"

echo.
echo JoeOS Command Center is starting at http://%JOEOS_BIND_HOST%:%JOEOS_BIND_PORT%
echo Lemonade Server remains private at %LEMONADE_BASE_URL%
if "%JOEOS_BIND_HOST%"=="127.0.0.1" (
  echo No Tailscale IPv4 address was detected, so access is limited to this computer.
) else (
  echo Open the JoeOS address on your iPhone while it is connected to the same tailnet.
)
echo Press Ctrl+C to stop JoeOS.
echo.

"%JOEOS_VENV%\Scripts\python.exe" -m uvicorn joeos_backend:app --host "%JOEOS_BIND_HOST%" --port "%JOEOS_BIND_PORT%" --no-access-log
if errorlevel 1 pause
