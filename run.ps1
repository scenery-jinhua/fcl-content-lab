# FCL one-click launcher (ASCII only, to avoid PowerShell 5.1 encoding issues)
# Usage: from the "project" folder run:  powershell -ExecutionPolicy Bypass -File .\run.ps1
#
# Why ASCII only: Windows PowerShell 5.1 reads .ps1 files as ANSI (GBK on a Chinese
# system) unless they have a BOM. Non-ASCII text inside the file can corrupt the
# quotes and cause "TerminatorExpectedAtEndOfString". English prompts are safe.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

Write-Host ""
Write-Host "=== FCL launcher ===" -ForegroundColor Cyan

# 1) virtual env
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[1/4] Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
} else {
    Write-Host "[1/4] Virtual environment found." -ForegroundColor DarkGray
}
$py = ".venv\Scripts\python.exe"

# 2) dependencies (skip if fastapi already importable)
& $py -c "import fastapi, sqlalchemy, httpx, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[2/4] Installing dependencies (first run may take a few minutes)..." -ForegroundColor Cyan
    & $py -m pip install -q --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
} else {
    Write-Host "[2/4] Dependencies already installed." -ForegroundColor DarkGray
}

# 3) seed data
Write-Host "[3/4] Seeding demo products..." -ForegroundColor Cyan
& $py scripts/seed.py

# 4) start server
Write-Host "[4/4] Starting server. Open http://127.0.0.1:8000 in your browser." -ForegroundColor Green
Write-Host "      API docs: http://127.0.0.1:8000/docs   (press Ctrl+C to stop)" -ForegroundColor DarkGray
Write-Host ""
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
