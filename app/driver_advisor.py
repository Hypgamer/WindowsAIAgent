from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .ai_provider import get_provider
from .models import Incident


APPROVED_DRIVER_DOMAINS = [
    "catalog.update.microsoft.com",
    "support.microsoft.com",
    "learn.microsoft.com",
    "dell.com",
    "hp.com",
    "lenovo.com",
    "asus.com",
    "acer.com",
    "intel.com",
    "nvidia.com",
    "amd.com",
    "realtek.com",
    "logitech.com",
    "qualcomm.com",
    "msi.com",
    "gigabyte.com",
    "asrock.com",
    "biostar.com.tw",
    "frameworkcomputer.com",
    "razer.com",
    "microsoft.com",
]


class InstalledDriver(BaseModel):
    device_name: str = ""
    manufacturer: str = ""
    device_class: str = ""
    driver_provider: str = ""
    driver_version: str = ""
    driver_date: str = ""
    inf_name: str = ""
    device_id: str = ""
    hardware_ids: list[str] = Field(default_factory=list)


class SystemProfile(BaseModel):
    system_manufacturer: str = ""
    system_model: str = ""
    system_sku: str = ""
    system_family: str = ""
    chassis_types: list[int] = Field(default_factory=list)
    form_factor: str = "unknown"
    motherboard_manufacturer: str = ""
    motherboard_product: str = ""
    motherboard_version: str = ""
    bios_version: str = ""
    lookup_identity: str = ""
    lookup_strategy: str = ""


class DriverSearchResult(BaseModel):
    # The advisory model is intentionally uncertainty-tolerant. A live web search
    # may be unable to establish a latest version/date for an exact OEM package.
    # In that case, null is a valid and preferred answer over a fabricated value.
    device_name: str = ""
    inf_name: str | None = None
    installed_version: str | None = None
    installed_date: str | None = None
    latest_version: str | None = None
    latest_date: str | None = None
    newer_available: bool | None = None
    hardware_match: str | None = "unknown"
    source_name: str | None = None
    source_url: str | None = None
    recommendation: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: str | None = None


class DriverSearchBatch(BaseModel):
    results: list[DriverSearchResult] = Field(default_factory=list)
    overall_summary: str | None = None


@dataclass
class DriverAdvice:
    installed: list[InstalledDriver]
    system_profile: SystemProfile
    results: list[DriverSearchResult]
    overall_summary: str
    provider: str
    model: str
    raw_response: str


def _run_powershell(script: str, timeout: int = 25) -> subprocess.CompletedProcess[str]:
    """Run a trusted, application-owned PowerShell collector script.

    Do not use this helper for AI-generated remediation code. The driver advisor
    only passes fixed scripts owned by this application. Passing the script as
    the -Command argument avoids an observed Windows PowerShell behavior where
    `-Command -` can exit successfully while producing no captured stdout.
    """
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        text=True,
        capture_output=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def collect_system_profile() -> SystemProfile:
    """Collect read-only machine identity used to choose the best official driver source."""
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$cs = Get-CimInstance Win32_ComputerSystem | Select-Object -First 1
$csp = Get-CimInstance Win32_ComputerSystemProduct | Select-Object -First 1
$bb = Get-CimInstance Win32_BaseBoard | Select-Object -First 1
$enc = Get-CimInstance Win32_SystemEnclosure | Select-Object -First 1
$bios = Get-CimInstance Win32_BIOS | Select-Object -First 1

if (-not $cs) { $cs = Get-WmiObject Win32_ComputerSystem | Select-Object -First 1 }
if (-not $csp) { $csp = Get-WmiObject Win32_ComputerSystemProduct | Select-Object -First 1 }
if (-not $bb) { $bb = Get-WmiObject Win32_BaseBoard | Select-Object -First 1 }
if (-not $enc) { $enc = Get-WmiObject Win32_SystemEnclosure | Select-Object -First 1 }
if (-not $bios) { $bios = Get-WmiObject Win32_BIOS | Select-Object -First 1 }

[PSCustomObject]@{
    system_manufacturer = [string]$cs.Manufacturer
    system_model = [string]$cs.Model
    system_sku = [string]$csp.SKUNumber
    system_family = [string]$cs.SystemFamily
    chassis_types = @($enc.ChassisTypes | ForEach-Object { [int]$_ })
    motherboard_manufacturer = [string]$bb.Manufacturer
    motherboard_product = [string]$bb.Product
    motherboard_version = [string]$bb.Version
    bios_version = [string]$bios.SMBIOSBIOSVersion
} | ConvertTo-Json -Depth 4 -Compress
"""
    completed = _run_powershell(script, timeout=20)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Could not read computer make/model information.")
    raw = completed.stdout.strip()
    if not raw:
        raise RuntimeError("Windows returned no computer identity information.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse computer identity information: {raw[:800]}") from exc

    chassis = data.get("chassis_types") or []
    if isinstance(chassis, int):
        chassis = [chassis]
    chassis = [int(x) for x in chassis if str(x).strip().isdigit()]

    # SMBIOS chassis values commonly used for portable/mobile computers.
    mobile_chassis = {8, 9, 10, 11, 14, 30, 31, 32}
    form_factor = "laptop" if any(x in mobile_chassis for x in chassis) else "desktop_or_other"

    manufacturer = str(data.get("system_manufacturer") or "").strip()
    model = str(data.get("system_model") or "").strip()
    sku = str(data.get("system_sku") or "").strip()
    board_mfr = str(data.get("motherboard_manufacturer") or "").strip()
    board_product = str(data.get("motherboard_product") or "").strip()

    generic_system_terms = {
        "system manufacturer", "system product name", "default string",
        "to be filled by o.e.m.", "oem", "unknown", "not applicable", "n/a",
    }
    system_identity_useful = (
        bool(manufacturer and model)
        and manufacturer.lower() not in generic_system_terms
        and model.lower() not in generic_system_terms
    )

    if form_factor == "laptop":
        lookup_identity = " ".join(x for x in [manufacturer, model, sku] if x)
        lookup_strategy = (
            "Portable/laptop system: prioritize the computer OEM support page for this exact make/model/SKU, "
            "then Microsoft Update Catalog or the audio hardware vendor if the OEM does not publish a clear match."
        )
    elif system_identity_useful:
        lookup_identity = " | ".join(
            x for x in [
                "System: " + " ".join(y for y in [manufacturer, model, sku] if y),
                "Motherboard: " + " ".join(y for y in [board_mfr, board_product] if y) if board_mfr or board_product else "",
            ] if x
        )
        lookup_strategy = (
            "Desktop or other non-portable system with a useful OEM model: check the OEM make/model first, "
            "and use the motherboard make/model plus device hardware IDs as a compatibility cross-check."
        )
    else:
        lookup_identity = " ".join(x for x in [board_mfr, board_product, str(data.get("motherboard_version") or "").strip()] if x)
        lookup_strategy = (
            "Desktop/custom/generic system: prioritize the motherboard manufacturer's support page for this exact board, "
            "then Microsoft Update Catalog or the audio hardware vendor, always checking the device hardware IDs."
        )

    return SystemProfile(
        system_manufacturer=manufacturer,
        system_model=model,
        system_sku=sku,
        system_family=str(data.get("system_family") or "").strip(),
        chassis_types=chassis,
        form_factor=form_factor,
        motherboard_manufacturer=board_mfr,
        motherboard_product=board_product,
        motherboard_version=str(data.get("motherboard_version") or "").strip(),
        bios_version=str(data.get("bios_version") or "").strip(),
        lookup_identity=lookup_identity,
        lookup_strategy=lookup_strategy,
    )


def _classes_for_incident(incident: Incident) -> list[str]:
    if "audio" in incident.kind:
        # MEDIA finds the actual audio driver packages; AudioEndpoint catches
        # endpoint-specific packages/virtual devices. We deduplicate below.
        return ["MEDIA", "AudioEndpoint"]
    if "display" in incident.kind or "peripheral" in incident.kind:
        return ["DISPLAY", "Monitor", "USB", "Image", "Camera"]
    if "printer" in incident.kind:
        return ["Printer", "PrintQueue"]
    return []


def _dedupe_driver_packages(drivers: list[InstalledDriver]) -> list[InstalledDriver]:
    """Collapse repeated endpoints that use the same installed driver package/version."""
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[InstalledDriver] = []
    for driver in drivers:
        # INF + version is the strongest package identity available here. If INF
        # is blank, retain device/provider/name so unrelated devices aren't merged.
        if driver.inf_name:
            key = (
                driver.inf_name.lower(),
                driver.driver_version.lower(),
                driver.driver_provider.lower(),
                "",
            )
        else:
            key = (
                "",
                driver.driver_version.lower(),
                driver.driver_provider.lower(),
                driver.device_name.lower(),
            )
        if key in seen:
            continue
        seen.add(key)
        unique.append(driver)
    return unique


def collect_installed_drivers(incident: Incident) -> list[InstalledDriver]:
    """Read-only inventory of installed driver packages relevant to the scenario.

    For audio, Windows exposes class metadata inconsistently across hardware and
    OEM images. To make discovery reliable we collect the signed-driver table
    broadly, then filter in Python using class names, class GUIDs, device names,
    providers and a Win32_SoundDevice/PnP fallback.
    """
    classes = _classes_for_incident(incident)
    if not classes:
        raise RuntimeError("This scenario does not currently support driver checking.")

    is_audio = "audio" in incident.kind.lower()

    script = r"""
$ErrorActionPreference = 'Continue'
$collectorErrors = @()

function Get-SafeCimOrWmi {
    param([string]$ClassName)
    try {
        $items = @(Get-CimInstance -ClassName $ClassName -ErrorAction Stop)
        if ($items.Count -gt 0) { return $items }
    } catch {
        $script:collectorErrors += "CIM ${ClassName}: $($_.Exception.Message)"
    }
    try {
        $items = @(Get-WmiObject -Class $ClassName -ErrorAction Stop)
        if ($items.Count -gt 0) { return $items }
    } catch {
        $script:collectorErrors += "WMI ${ClassName}: $($_.Exception.Message)"
    }
    return @()
}

$rawDrivers = @(Get-SafeCimOrWmi 'Win32_PnPSignedDriver')
$drivers = @($rawDrivers | ForEach-Object {
    [PSCustomObject]@{
        device_name     = [string]$_.DeviceName
        friendly_name   = [string]$_.FriendlyName
        manufacturer    = [string]$_.Manufacturer
        device_class    = [string]$_.DeviceClass
        class_guid      = [string]$_.ClassGuid
        driver_provider = [string]$_.DriverProviderName
        driver_version  = [string]$_.DriverVersion
        driver_date     = if ($_.DriverDate) { [string]$_.DriverDate } else { '' }
        inf_name        = [string]$_.InfName
        device_id       = [string]$_.DeviceID
        description     = [string]$_.Description
    }
})

$rawSound = @(Get-SafeCimOrWmi 'Win32_SoundDevice')
$soundIds = @($rawSound | ForEach-Object { [string]$_.PNPDeviceID } | Where-Object { $_ })

$pnpAudioIds = @()
try {
    if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) {
        $pnpAudioIds += @(Get-PnpDevice -Class Media -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.InstanceId })
        $pnpAudioIds += @(Get-PnpDevice -Class AudioEndpoint -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.InstanceId })
    }
} catch {
    $collectorErrors += "PnP: $($_.Exception.Message)"
}
$pnpAudioIds = @($pnpAudioIds | Where-Object { $_ } | Select-Object -Unique)

[PSCustomObject]@{
    drivers = @($drivers)
    sound_ids = @($soundIds)
    pnp_audio_ids = @($pnpAudioIds)
    driver_count = @($drivers).Count
    sound_device_count = @($rawSound).Count
    collector_errors = @($collectorErrors)
} | ConvertTo-Json -Depth 6 -Compress
"""
    completed = _run_powershell(script, timeout=35)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Windows driver inventory command failed.")
    raw = completed.stdout.strip()
    if not raw:
        raise RuntimeError("Windows returned no signed-driver inventory data.")
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse Windows driver inventory: {raw[:800]}") from exc

    rows = payload.get("drivers", []) if isinstance(payload, dict) else []
    collector_errors = payload.get("collector_errors", []) if isinstance(payload, dict) else []
    if isinstance(collector_errors, str):
        collector_errors = [collector_errors]
    if not rows:
        detail = "; ".join(str(x) for x in collector_errors if x)
        counts = (
            f"driver_count={payload.get('driver_count', 0)}, "
            f"sound_device_count={payload.get('sound_device_count', 0)}"
            if isinstance(payload, dict) else ""
        )
        raise RuntimeError(
            "Windows returned no signed-driver rows. "
            + counts
            + (f" Collector details: {detail}" if detail else "")
        )
    if isinstance(rows, dict):
        rows = [rows]
    sound_ids = payload.get("sound_ids", []) if isinstance(payload, dict) else []
    pnp_audio_ids = payload.get("pnp_audio_ids", []) if isinstance(payload, dict) else []
    if isinstance(sound_ids, str):
        sound_ids = [sound_ids]
    if isinstance(pnp_audio_ids, str):
        pnp_audio_ids = [pnp_audio_ids]
    sound_ids = {str(x).lower() for x in sound_ids if x}
    pnp_audio_ids = {str(x).lower() for x in pnp_audio_ids if x}

    audio_guids = {
        "{4d36e96c-e325-11ce-bfc1-08002be10318}",
        "{c166523c-fe0c-4a94-a586-f1a80cfbbf3e}",
    }
    audio_terms = (
        "audio", "sound", "speaker", "microphone", "headset", "headphone",
        "smart sound", "high definition", "realtek", "nahimic", "sonic studio",
        "dolby", "waves", "conexant", "cirrus", "usb audio", "bluetooth audio",
    )
    wanted_classes = {c.lower() for c in classes}

    matched_rows: list[dict[str, Any]] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        device_class = str(item.get("device_class") or "").lower()
        class_guid = str(item.get("class_guid") or "").lower()
        device_id = str(item.get("device_id") or "").lower()
        haystack = " ".join(
            str(item.get(k) or "")
            for k in ("device_name", "friendly_name", "description", "manufacturer", "driver_provider", "device_class")
        ).lower()

        relevant = device_class in wanted_classes
        if is_audio:
            relevant = (
                relevant
                or class_guid in audio_guids
                or device_id in sound_ids
                or device_id in pnp_audio_ids
                or any(term in haystack for term in audio_terms)
            )
        if relevant:
            matched_rows.append(item)

    hardware_map: dict[str, list[str]] = {}
    matched_ids = [str(x.get("device_id") or "") for x in matched_rows if x.get("device_id")]
    if matched_ids:
        ids_json = json.dumps(matched_ids)
        hardware_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$ids = ConvertFrom-Json @'
{ids_json}
'@
$out = foreach ($id in $ids) {{
    $prop = Get-PnpDeviceProperty -InstanceId $id -KeyName 'DEVPKEY_Device_HardwareIds' -ErrorAction SilentlyContinue
    [PSCustomObject]@{{ device_id = [string]$id; hardware_ids = @($prop.Data) }}
}}
$out | ConvertTo-Json -Depth 4 -Compress
"""
        try:
            hw = _run_powershell(hardware_script, timeout=20)
            if hw.returncode == 0 and hw.stdout.strip():
                hp = json.loads(hw.stdout.strip())
                if isinstance(hp, dict):
                    hp = [hp]
                for entry in hp or []:
                    did = str(entry.get("device_id") or "").lower()
                    vals = entry.get("hardware_ids") or []
                    if isinstance(vals, str):
                        vals = [vals]
                    hardware_map[did] = [str(v) for v in vals if v]
        except Exception:
            pass

    drivers: list[InstalledDriver] = []
    for item in matched_rows:
        drivers.append(
            InstalledDriver(
                device_name=str(item.get("device_name") or item.get("friendly_name") or item.get("description") or "Unknown device"),
                manufacturer=str(item.get("manufacturer") or ""),
                device_class=str(item.get("device_class") or ""),
                driver_provider=str(item.get("driver_provider") or ""),
                driver_version=str(item.get("driver_version") or ""),
                driver_date=str(item.get("driver_date") or ""),
                inf_name=str(item.get("inf_name") or ""),
                device_id=str(item.get("device_id") or ""),
                hardware_ids=hardware_map.get(str(item.get("device_id") or "").lower(), []),
            )
        )

    return _dedupe_driver_packages(drivers)

def _clean_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json\n"):
            text = text[5:].lstrip()
    return text


def _driver_search_prompt(incident: Incident, drivers: list[InstalledDriver], system_profile: SystemProfile) -> str:
    compact = [d.model_dump() for d in drivers]
    scope_note = (
        "This is an AUDIO scenario. Evaluate EVERY installed driver package in the inventory, not just the device mentioned in the simulated incident. "
        "Audio systems commonly contain multiple packages (Realtek/Intel/AMD/NVIDIA/USB/Bluetooth/virtual endpoints)."
        if "audio" in incident.kind
        else "Evaluate every driver package provided in the inventory that could reasonably relate to the incident."
    )
    return f"""
You are performing a DRIVER ADVISORY lookup for a Windows standard user.

Current incident:
{incident.model_dump_json(indent=2)}

Computer identity and preferred lookup strategy:
{system_profile.model_dump_json(indent=2)}

Installed driver inventory:
{json.dumps(compact, indent=2)}

{scope_note}

Use live web search. Search ONLY the configured approved domains.

For EACH installed driver package above:
1. Use the computer identity and lookup_strategy above to decide where to look first.
2. Identify the exact hardware/package using hardware IDs and INF/provider information whenever possible.
3. For a laptop, prioritize the exact computer OEM make/model/SKU. For a branded desktop, prioritize the OEM model and cross-check the motherboard. For a custom/generic desktop, prioritize the exact motherboard manufacturer/product.
4. Find the most recent driver that is clearly applicable to that exact computer/board/hardware from the computer OEM, motherboard OEM, Microsoft Update Catalog, or the hardware manufacturer.
5. Compare the installed version/date with the latest trustworthy applicable version/date.
6. If a reliable comparison cannot be established, make the best source-selection guess you can but return newer_available=null rather than inventing a version or compatibility claim.

Do NOT recommend third-party driver-download sites. Do NOT invent version numbers, release dates, compatibility, or URLs. Do NOT skip a package merely because it is not the simulated failing device.

This is advisory only. Do NOT provide installation commands and do NOT claim that a driver should automatically be installed. The user is a non-admin user.

Return JSON only with exactly these top-level keys:
results, overall_summary

results must contain ONE object for EACH installed driver package supplied above, in the same general order. Each result object must contain exactly these keys:
device_name, inf_name, installed_version, installed_date, latest_version, latest_date, newer_available, hardware_match, source_name, source_url, recommendation, confidence, notes

Rules:
- confidence must be a number from 0.0 to 1.0
- newer_available must be true, false, or null if it cannot be determined
- hardware_match must briefly say exact, compatible, name-only, or unknown
- source_url must be an official source URL from the approved search domains, or an empty string
- recommendation must be plain language for a non-technical user
- overall_summary should tell the user how many packages were checked, how many appear current, how many have newer applicable drivers, and how many could not be verified
""".strip()


def _sanitize_result_url(result: DriverSearchResult) -> None:
    if not result.source_url:
        return
    host = (urlparse(result.source_url).hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in APPROVED_DRIVER_DOMAINS):
        result.source_url = ""
        result.source_name = ""
        result.notes = (
            result.notes + " Source URL was removed because it was outside the approved domain list."
        ).strip()


def check_driver_online(incident: Incident, drivers: list[InstalledDriver], system_profile: SystemProfile | None = None) -> DriverAdvice:
    if not drivers:
        raise RuntimeError("No relevant installed drivers were found on this computer.")

    system_profile = system_profile or collect_system_profile()
    provider = get_provider()
    chunks = []
    completed = False
    with provider.client.responses.create(
        model=provider.model,
        tools=[
            {
                "type": "web_search",
                "filters": {"allowed_domains": APPROVED_DRIVER_DOMAINS},
                "search_context_size": "medium",
            }
        ],
        tool_choice="required",
        include=["web_search_call.action.sources"],
        input=[{"role": "user", "content": _driver_search_prompt(incident, drivers, system_profile)}],
        store=False,
        stream=True,
    ) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                chunks.append(event.delta)
            elif event.type == "response.failed":
                error = event.response.error
                code = error.code if error else "unknown_error"
                raise RuntimeError(f"Driver advisory AI response failed: {code}")
            elif event.type == "response.completed":
                completed = True
    if not completed:
        raise RuntimeError("Driver advisory AI stream ended without response.completed.")
    raw = "".join(chunks).strip()
    if not raw:
        raise RuntimeError("The AI completed the driver search without returning an answer.")
    try:
        batch = DriverSearchBatch.model_validate(json.loads(_clean_json(raw)))
    except Exception as exc:
        # Surface the actual schema problem first. The raw answer is still kept
        # short so logs stay readable and do not swallow the useful validation error.
        raise RuntimeError(
            "AI returned a driver advisory that could not be parsed. "
            f"Validation detail: {exc}. Response preview: {raw[:700]}"
        ) from exc

    for result in batch.results:
        _sanitize_result_url(result)

    return DriverAdvice(
        installed=drivers,
        system_profile=system_profile,
        results=batch.results,
        overall_summary=batch.overall_summary,
        provider=provider.kind,
        model=provider.model,
        raw_response=raw,
    )
