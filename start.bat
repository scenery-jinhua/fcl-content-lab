@echo off
REM FCL launcher for people who don't want to deal with PowerShell execution policy.
REM Just double-click this file.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment...
    python -m venv .venv
)

echo [2/4] Checking dependencies...
".venv\Scripts\python.exe" -c "import fastapi, sqlalchemy, httpx, uvicorn" 2>nul
if errorlevel 1 (
    echo Installing dependencies, please wait...
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
)

echo [3/4] Seeding demo products...
".venv\Scripts\python.exe" scripts\seed.py

echo [4/4] Starting server: http://127.0.0.1:8000
echo        API docs:    http://127.0.0.1:8000/docs
echo        Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

pause
