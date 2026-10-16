$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) { python -m venv .venv }
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

# Debug build retains a console window so packaging/import errors are visible.
& ".\.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --console `
  --name WindowsAISupportAgent-Debug `
  --collect-all truststore `
  --collect-all openai `
  --collect-all pydantic `
  --collect-all jwt `
  --collect-all cryptography `
  windows_ai_support_agent.py

Write-Host "Debug build: $PSScriptRoot\dist\WindowsAISupportAgent-Debug.exe"
