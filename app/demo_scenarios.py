from __future__ import annotations

import subprocess
import time
import uuid
from datetime import datetime, timezone

from .models import Incident


SCENARIOS = {
    "1": ("Frozen Windows Explorer", "explorer"),
    "2": ("Frozen application", "application"),
    "3": ("Network / DNS problem", "network"),
    "4": ("Audio / microphone problem", "audio"),
    "5": ("Printer unavailable", "printer"),
    "6": ("Monitor / USB peripheral problem", "peripheral"),
}


def _id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"demo-{prefix}-{stamp}-{uuid.uuid4().hex[:6]}"


def _start_demo_notepad() -> int | None:
    """Launch a dedicated Notepad instance for the app-hang demo.

    The PID is returned so the AI can be instructed to target only this demo
    process instead of every Notepad process owned by the user.
    """
    try:
        process = subprocess.Popen(["notepad.exe"])
        time.sleep(0.8)
        return process.pid
    except Exception:
        return None


def build_demo_incident(code: str) -> Incident:
    if code == "1":
        return Incident(
            incident_id=_id("explorer"),
            kind="demo_explorer_unresponsive",
            summary="Windows Explorer appears to be unresponsive (simulated diagnostic)",
            details=(
                "DEMO DATA ONLY. The detection is simulated; the AI diagnosis and remediation must be live.\n"
                "Observed process: explorer.exe\n"
                "Process present: yes\n"
                "Responsive: no\n"
                "User privilege: standard user\n"
                "Allowed remediation policy: low-risk current-user actions only. Restarting Explorer is permitted; "
                "no admin elevation, file changes, registry changes, service changes, downloads, or security changes."
            ),
            process_name="explorer",
        )

    if code == "2":
        pid = _start_demo_notepad()
        pid_text = str(pid) if pid else "unavailable"
        return Incident(
            incident_id=_id("app"),
            kind="demo_application_unresponsive",
            summary="A demo Notepad instance appears to be unresponsive (simulated diagnostic)",
            details=(
                "DEMO DATA ONLY. The unresponsive state is simulated; the AI diagnosis and remediation must be live.\n"
                "Application: Notepad\n"
                f"Dedicated demo process PID: {pid_text}\n"
                "Responsive: no\n"
                "User privilege: standard user\n"
                "Important: if a remediation stops a process, it must target ONLY the dedicated demo PID above. "
                "The user must be warned that unsaved work in that demo application could be lost."
            ),
            process_name="notepad",
            process_id=pid,
        )

    if code == "3":
        return Incident(
            incident_id=_id("network"),
            kind="demo_network_dns_failure",
            summary="Internet name resolution is failing (simulated diagnostic)",
            details=(
                "DEMO DATA ONLY. The diagnostics are simulated; the AI diagnosis and remediation must be live.\n"
                "Wi-Fi adapter: connected\n"
                "IPv4 address: present\n"
                "Default gateway: present\n"
                "DNS lookup for a public hostname: failed\n"
                "HTTPS reachability by hostname: failed\n"
                "User privilege: standard user\n"
                "Policy: do not change adapter configuration, DNS servers, routes, firewall, services, or security settings. "
                "A safe remediation may open the appropriate Windows Settings page for the user."
            ),
        )

    if code == "4":
        return Incident(
            incident_id=_id("audio"),
            kind="demo_audio_device_missing",
            summary="Expected USB headset is not available (simulated diagnostic)",
            details=(
                "DEMO DATA ONLY. The diagnostics are simulated; the AI diagnosis and remediation must be live.\n"
                "Built-in speakers: present / OK\n"
                "Built-in microphone: present / OK\n"
                "Expected USB headset: not present\n"
                "User privilege: standard user\n"
                "Policy: do not install/remove drivers, change services, edit the registry, or disable devices. "
                "A safe remediation may open the relevant Windows Sound Settings page."
            ),
        )

    if code == "5":
        return Incident(
            incident_id=_id("printer"),
            kind="demo_printer_offline",
            summary="Configured printer reports as offline (simulated diagnostic)",
            details=(
                "DEMO DATA ONLY. The diagnostics are simulated; the AI diagnosis and remediation must be live.\n"
                "Printer name: Office-Color\n"
                "Installed: yes\n"
                "Default printer: yes\n"
                "Reported state: offline\n"
                "User privilege: standard user\n"
                "Policy: do not restart services, delete print jobs, remove printers, install drivers, or modify system settings. "
                "A safe remediation may open the Windows Printers & scanners Settings page."
            ),
        )

    if code == "6":
        return Incident(
            incident_id=_id("peripheral"),
            kind="demo_external_display_missing",
            summary="Expected external display is not detected (simulated diagnostic)",
            details=(
                "DEMO DATA ONLY. The diagnostics are simulated; the AI diagnosis and remediation must be live.\n"
                "Integrated display: present\n"
                "Graphics adapter: present / OK\n"
                "Expected external display: not detected\n"
                "User privilege: standard user\n"
                "Policy: do not install/remove drivers, disable devices, modify registry settings, or request elevation. "
                "A safe remediation may open the Windows Display Settings page."
            ),
        )

    raise ValueError(f"Unknown demo scenario: {code}")


def choose_demo_scenario() -> str | None:
    print("\nWindows AI Support Agent — Demo Lab")
    print("The Windows symptom is simulated. AI diagnosis/remediation is LIVE.\n")
    for code, (title, _) in SCENARIOS.items():
        print(f"  {code}. {title}")
    print("  Q. Quit")

    while True:
        value = input("\nChoose a demo scenario: ").strip().lower()
        if value == "q":
            return None
        if value in SCENARIOS:
            return value
        print("Please choose 1-6 or Q.")
