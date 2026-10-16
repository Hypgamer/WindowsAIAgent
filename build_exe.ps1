$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Windows AI Support Agent - EXE Builder ===" -ForegroundColor Cyan
Write-Host "Project: $PSScriptRoot"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found on PATH. Install Python 3.11+ from python.org and try again."
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating build virtual environment..."
    python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Host "Running source compile check..."
& ".\.venv\Scripts\python.exe" -m compileall -q app windows_ai_support_agent.py

Write-Host "Building WindowsAISupportAgent.exe..." -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean WindowsAISupportAgent.spec

$exe = Join-Path $PSScriptRoot "dist\WindowsAISupportAgent.exe"
if (-not (Test-Path $exe)) {
    throw "Build finished without producing $exe"
}

Write-Host ""
Write-Host "Build complete:" -ForegroundColor Green
Write-Host $exe
Write-Host ""
Write-Host "Recommended test: copy the EXE to a clean folder/user profile and launch it."
