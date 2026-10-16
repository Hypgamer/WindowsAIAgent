import json
import re
import subprocess
from .models import Incident, ValidationResult

# Defense in depth: reject dangerous command families even if the model labels them safe.
DENY_TERMS = {
    "remove-item", "del ", "erase ", "rmdir", "format-volume", "clear-disk",
    "initialize-disk", "set-partition", "remove-itemproperty", "set-itemproperty",
    "new-itemproperty", "invoke-expression", "iex", "-encodedcommand", "-enc ",
    "-verb runas", "cmd.exe", "cmd /c", "schtasks", "sc.exe", "net user",
    "net localgroup", "set-executionpolicy", "disable-", "uninstall-",
    "invoke-webrequest", "curl ", "wget ", "bitsadmin", "certutil", "reg.exe",
    "new-localuser", "remove-localuser", "add-localgroupmember", "remove-localgroupmember",
    "set-netfirewall", "new-netfirewall", "remove-netfirewall", "manage-bde",
    "stop-service", "start-service", "restart-service", "set-service",
    "wscript", "cscript", "mshta", "rundll32",
    "-argumentlist", "-credential", "-workingdirectory", "-verb "
}

ALLOW_COMMANDS = {
    "Get-Process", "Stop-Process", "Start-Process", "Get-Service", "Get-CimInstance",
    "Get-NetAdapter", "Get-NetIPConfiguration", "Get-DnsClientCache", "Resolve-DnsName",
    "Test-NetConnection", "Get-PnpDevice", "Get-Printer", "Get-WinEvent", "Get-Date",
    "Where-Object", "Select-Object", "ForEach-Object", "Out-String", "Write-Output",
    # Harmless delay is allowed so a restart sequence can pause briefly between stop/start.
    "Start-Sleep",
}

ALLOWED_STOP_PROCESSES = {"explorer", "ms-teams", "teams", "outlook", "chrome", "msedge", "firefox"}
ALLOWED_START_TARGETS = {
    "explorer.exe",
    "notepad.exe",
    "ms-teams.exe",
    "teams.exe",
    "outlook.exe",
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "ms-settings:network-wifi",
    "ms-settings:network-status",
    "ms-settings:sound",
    "ms-settings:printers",
    "ms-settings:display",
    "ms-settings:bluetooth",
    "ms-settings:connecteddevices",
}

# The parser script reads the generated remediation from STDIN. This avoids having to embed
# arbitrary model-generated PowerShell inside another PowerShell command line.
AST_INSPECTOR = r'''
$ScriptText = [Console]::In.ReadToEnd()
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($ScriptText,[ref]$tokens,[ref]$errors)
$commands = @()
$ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object {
    $name = $_.GetCommandName()
    if ($name) { $commands += $name }
}
[pscustomobject]@{
    ParseErrors = @($errors | ForEach-Object { $_.Message })
    Commands = @($commands)
} | ConvertTo-Json -Compress
'''


def _inspect_ast(script: str, timeout: int = 5) -> tuple[list[str], list[str]]:
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", AST_INSPECTOR],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return [], [f"PowerShell safety parser timed out after {timeout} seconds"]
    except OSError as exc:
        return [], [f"PowerShell safety parser could not start: {exc}"]

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "PowerShell parser failed"
        return [], [detail]

    output = completed.stdout.strip()
    if not output:
        return [], ["PowerShell safety parser returned no output"]

    try:
        parsed = json.loads(output)
    except Exception:
        return [], [f"Could not parse PowerShell AST inspection output: {output[:200]}"]

    commands = parsed.get("Commands") or []
    errors = parsed.get("ParseErrors") or []
    if isinstance(commands, str):
        commands = [commands]
    if isinstance(errors, str):
        errors = [errors]
    return commands, errors


def _normalize_start_target(target: str) -> str:
    """Normalize only explicitly recognized benign Explorer paths; reject other expansions."""
    cleaned = target.strip().strip('"\'')
    lowered = cleaned.lower().replace("/", "\\")
    # Explorer is a special case frequently emitted as $env:WINDIR\explorer.exe.
    if lowered in {"$env:windir\\explorer.exe", "${env:windir}\\explorer.exe"}:
        return "explorer.exe"
    return cleaned


def _extract_start_targets(script: str) -> list[str]:
    pattern = re.compile(
        r"Start-Process\s+(?:-FilePath\s+)?(?:['\"](?P<quoted>[^'\"]+)['\"]|(?P<bare>[^\s;|]+))",
        re.IGNORECASE,
    )
    targets = []
    for match in pattern.finditer(script):
        raw = (match.group("quoted") or match.group("bare") or "").strip()
        targets.append(_normalize_start_target(raw))
    return targets


def _stop_process_is_safe(script: str, incident: Incident | None) -> bool:
    lowered = script.lower()
    if incident and incident.process_id is not None:
        # A demo application process must be targeted by its exact PID.
        if re.search(rf"(?i)stop-process\b[^;|\n]*\s-id\s+{incident.process_id}\b", script):
            return True
    return any(
        f"-name {p}" in lowered or f"-name '{p}'" in lowered or f'-name "{p}"' in lowered
        for p in ALLOWED_STOP_PROCESSES
    )


def _sleep_is_safe(script: str) -> bool:
    """Allow only short literal sleeps (1-5 seconds)."""
    for match in re.finditer(r"(?i)Start-Sleep\b([^;|\n]*)", script):
        args = match.group(1)
        seconds = re.search(r"(?i)-Seconds\s+(\d+)", args)
        milliseconds = re.search(r"(?i)-Milliseconds\s+(\d+)", args)
        if seconds:
            if 1 <= int(seconds.group(1)) <= 5:
                continue
            return False
        if milliseconds:
            if 100 <= int(milliseconds.group(1)) <= 5000:
                continue
            return False
        return False
    return True


def validate_script(script: str, incident: Incident | None = None) -> ValidationResult:
    reasons: list[str] = []
    if not script.strip():
        return ValidationResult(allowed=False, reasons=["No remediation script was provided."])

    lowered = script.lower()
    for term in DENY_TERMS:
        if term in lowered:
            reasons.append(f"Blocked term detected: {term}")

    commands, parse_errors = _inspect_ast(script)
    reasons.extend(f"PowerShell parse/validation error: {e}" for e in parse_errors)

    for command in commands:
        if command not in ALLOW_COMMANDS:
            reasons.append(f"Command is not allowlisted: {command}")

    if "Stop-Process" in commands and not _stop_process_is_safe(script, incident):
        reasons.append("Stop-Process target is not an approved process or the dedicated demo PID.")

    if "Start-Sleep" in commands and not _sleep_is_safe(script):
        reasons.append("Start-Sleep is allowed only for a literal delay of 1-5 seconds.")

    if "Start-Process" in commands:
        targets = _extract_start_targets(script)
        if not targets:
            reasons.append("Could not determine the Start-Process target.")
        approved = {value.lower() for value in ALLOWED_START_TARGETS}
        for target in targets:
            if target.lower() not in approved:
                reasons.append(f"Start-Process target is not approved: {target}")

    return ValidationResult(allowed=not reasons, reasons=reasons, commands=commands)


def run_script(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
