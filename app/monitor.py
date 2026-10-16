import json
import subprocess
import time
import uuid

from .config import POLL_SECONDS
from .models import Incident

EVENT_QUERY = r'''
$start = (Get-Date).AddSeconds(-45)
Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=$start; Id=1000,1002} -ErrorAction SilentlyContinue |
    Select-Object -First 10 Id, ProviderName, TimeCreated, Message |
    ConvertTo-Json -Compress
'''

_seen = set()


def next_incidents():
    try:
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", EVENT_QUERY],
            capture_output=True,
            text=True,
            timeout=10,
        )
        raw = p.stdout.strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        incidents = []
        for event in data:
            key = f"{event.get('TimeCreated')}|{event.get('Id')}|{event.get('Message')}"
            if key in _seen:
                continue
            _seen.add(key)
            message = (event.get("Message") or "")[:3000]
            incidents.append(
                Incident(
                    incident_id=str(uuid.uuid4()),
                    kind="application_event",
                    summary=f"Windows recorded application event {event.get('Id')} from {event.get('ProviderName')}",
                    details=message,
                )
            )
        return incidents
    except Exception:
        return []


def wait():
    time.sleep(POLL_SECONDS)
