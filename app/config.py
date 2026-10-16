from __future__ import annotations

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

APP_NAME = "Windows AI Support Agent"
APP_FOLDER_NAME = "WindowsAISupportAgent"

# Source-code mode keeps configuration beside the project for an easy lab
# experience. A frozen executable must not write into PyInstaller's temporary
# extraction directory (or Program Files), so it stores per-user data under
# %LOCALAPPDATA% instead.
SOURCE_ROOT = Path(__file__).resolve().parent.parent
IS_FROZEN = bool(getattr(sys, "frozen", False))

if IS_FROZEN:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        DATA_DIR = Path(local_app_data) / APP_FOLDER_NAME
    else:
        DATA_DIR = Path.home() / "AppData" / "Local" / APP_FOLDER_NAME
    DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    DATA_DIR = SOURCE_ROOT

ENV_PATH = DATA_DIR / ".env"
LOG_DIR = DATA_DIR / "logs"

_DEFAULT_ENV = {
    "AI_PROVIDER": "auto",
    "OPENAI_API_KEY": "",
    "OPENAI_MODEL": "gpt-5.6",
    "POLL_SECONDS": "15",
    "DEMO_MODE": "true",
    "SETUP_COMPLETE": "false",
}


def _ensure_env_exists() -> None:
    if ENV_PATH.exists():
        return
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in _DEFAULT_ENV.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def reload_settings() -> None:
    global AI_PROVIDER, OPENAI_API_KEY, OPENAI_MODEL, POLL_SECONDS, DEMO_MODE, SETUP_COMPLETE
    _ensure_env_exists()
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    AI_PROVIDER = os.environ.get("AI_PROVIDER", "auto").strip().lower()
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6").strip()
    POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "15"))
    DEMO_MODE = os.environ.get("DEMO_MODE", "true").lower() == "true"
    SETUP_COMPLETE = os.environ.get("SETUP_COMPLETE", "false").lower() == "true"


def update_env(values: dict[str, str]) -> None:
    """Update selected .env keys while preserving unrelated settings/comments where possible."""
    _ensure_env_exists()
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    pending = dict(values)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)
    if pending:
        if out and out[-1].strip():
            out.append("")
        for key, value in pending.items():
            out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    for key, value in values.items():
        os.environ[key] = value
    reload_settings()


reload_settings()
