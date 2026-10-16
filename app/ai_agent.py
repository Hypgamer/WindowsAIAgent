import json
from dataclasses import dataclass

from .ai_provider import get_provider
from .models import Incident, Proposal

SYSTEM = r"""
You are the reasoning component of a Windows first-line support agent for a NON-ADMIN, NON-TECHNICAL user.

The incident data may be real diagnostics or explicitly marked DEMO DATA. Demo data simulates only the Windows symptom. You MUST still perform the diagnosis and remediation reasoning yourself; do not assume a canned answer.

Your job is to propose at most one low-risk remediation using PowerShell, or propose no action when a safe remediation is not appropriate.

Hard rules:
- Never delete, move, purge, clean up, overwrite, or modify user files or system files.
- Never modify the registry.
- Never install or uninstall software or drivers.
- Never change Windows services, firewall, Defender/EDR, BitLocker, users/groups, permissions, scheduled tasks, execution policy, disks/partitions, BIOS, firmware, or Windows Update policy.
- Never request elevation or administrator credentials.
- Never download content or execute remote code.
- Never use encoded/obfuscated PowerShell, Invoke-Expression, cmd.exe, wscript, cscript, mshta, rundll32, reg.exe, sc.exe, schtasks, certutil, bitsadmin, curl, wget, or Invoke-WebRequest.
- Prefer read-only diagnostics when a safe remediation is not justified.
- A user-impacting remediation must be reversible and possible as the current standard user.
- Explain the proposed action in plain language suitable for someone with no IT knowledge.
- Explain meaningful side effects BEFORE the user approves the remediation.
- If the incident includes a dedicated demo process PID, any Stop-Process remediation MUST target that PID only. Do not stop every process with the same name.
- Opening an appropriate Windows Settings page with an ms-settings URI is allowed when it helps the user continue safely.
- Prefer simple, direct PowerShell over complex variables, loops, session filtering, or dynamic paths.
- For a simulated unresponsive Windows Explorer incident, prefer the minimal remediation: Stop-Process -Name explorer -Force; Start-Process explorer.exe
- If a short pause is truly needed between stopping and restarting an app, Start-Sleep may be used only for 1 to 5 seconds.
- If no safe remediation exists, set powershell to an empty string and risk to requires_admin or prohibited as appropriate.

Return JSON only with exactly these keys:
problem, user_explanation, impact, powershell, verification_powershell, risk, requires_admin, confidence

Requirements:
- risk must be one of: diagnostic, safe_user_action, requires_admin, prohibited, unknown
- confidence MUST be a JSON number from 0.0 to 1.0 (for example 0.92)
- never use words such as low, medium, or high for confidence
- powershell and verification_powershell must be plain PowerShell strings with no Markdown fences
"""


@dataclass
class ProposalResult:
    proposal: Proposal
    provider: str
    model: str
    raw_response: str


def _response_text(provider, incident: Incident) -> str:
    chunks = []
    completed = False
    with provider.client.responses.create(
        model=provider.model,
        instructions=SYSTEM,
        input=[{"role": "user", "content": f"Incident:\n{incident.model_dump_json(indent=2)}"}],
        store=False,
        stream=True,
    ) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                chunks.append(event.delta)
            elif event.type == "response.failed":
                error = event.response.error
                code = error.code if error else "unknown_error"
                raise RuntimeError(f"AI response failed: {code}")
            elif event.type == "response.completed":
                completed = True
    if not completed:
        raise RuntimeError("AI stream ended without response.completed.")
    return "".join(chunks).strip()


def _clean_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"): lines = lines[1:]
        if lines and lines[-1].strip() == "```": lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json\n"):
            text = text[5:].lstrip()
    return text


def propose(incident: Incident) -> ProposalResult:
    provider = get_provider()
    raw = _response_text(provider, incident)
    clean = _clean_json(raw)
    try:
        payload = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI returned invalid JSON: {raw[:500]}") from exc

    proposal = Proposal.model_validate(payload)
    return ProposalResult(
        proposal=proposal,
        provider=provider.kind,
        model=provider.model,
        raw_response=raw,
    )
