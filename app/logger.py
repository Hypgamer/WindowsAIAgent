import json
from datetime import datetime, timezone

from .config import LOG_DIR

LOG_PATH = LOG_DIR / "agent.jsonl"


def log_event(event: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
