# Windows AI Support Agent — Source

This package contains only the application source and the files needed to run or build it on Windows.

## Run from source

Open PowerShell in this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python -m app.main
```

On first launch, the GUI can configure **Sign in with ChatGPT** or an **OpenAI API key**.

## Build the Windows EXE

The same `requirements.txt` includes PyInstaller. Build with:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build_exe.ps1
```

Output:

```text
dist\WindowsAISupportAgent.exe
```

For a console-enabled troubleshooting build:

```powershell
.\build_debug_exe.ps1
```

## Important files

```text
app/                         Application source
windows_ai_support_agent.py  PyInstaller entry point
requirements.txt             Runtime + build dependencies
WindowsAISupportAgent.spec   PyInstaller build specification
build_exe.ps1                Normal GUI EXE build
build_debug_exe.ps1          Debug/console EXE build
.env.example                 Configuration template
.gitignore                   Excludes secrets/build artifacts
```

## Credentials

Do not distribute a populated `.env` file. API keys and locally stored ChatGPT credentials are user-specific and must not be committed or shared.
