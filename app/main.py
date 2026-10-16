from .ai_agent import propose
from .approval import ask_for_approval
from .config import DEMO_MODE
from .demo_scenarios import build_demo_incident, choose_demo_scenario
from .gui import launch_gui
from .logger import log_event
from .monitor import next_incidents, wait
from .powershell import validate_script, run_script


def handle(incident):
    print(f"\nDetected: {incident.summary}")
    log_event({"stage": "detected", "incident": incident.model_dump()})

    try:
        result = propose(incident)
        proposal = result.proposal
    except Exception as exc:
        print(f"AI proposal failed: {exc}")
        print("No fallback remediation will be used. Nothing will be executed.")
        log_event({"stage": "ai_error", "incident_id": incident.incident_id, "error": str(exc)})
        return

    print(f"AI response received — provider={result.provider}, model={result.model}")
    print(f"AI diagnosis: {proposal.problem}")
    print(f"AI confidence: {proposal.confidence:.2f}")
    print(f"AI proposed PowerShell: {proposal.powershell or '[no remediation proposed]'}")

    log_event({
        "stage": "proposed",
        "incident_id": incident.incident_id,
        "provider": result.provider,
        "model": result.model,
        "raw_ai_response": result.raw_response,
        "proposal": proposal.model_dump(),
    })

    if proposal.requires_admin or proposal.risk in {"requires_admin", "prohibited", "unknown"}:
        print("Proposal requires escalation; no execution attempted.")
        log_event({"stage": "escalated", "incident_id": incident.incident_id, "reason": proposal.risk})
        return

    print("Running local PowerShell safety validation...")
    try:
        validation = validate_script(proposal.powershell, incident)
    except Exception as exc:
        print(f"Local safety validation failed: {exc}")
        print("Nothing was executed. Returning to the demo menu.")
        log_event({"stage": "validation_error", "incident_id": incident.incident_id, "error": str(exc)})
        return
    print("Local safety validation complete.")
    log_event({"stage": "validated", "incident_id": incident.incident_id, "validation": validation.model_dump()})

    if not validation.allowed:
        print("Local safety validator BLOCKED the AI-generated command:")
        for reason in validation.reasons:
            print(f"  - {reason}")

    approved = ask_for_approval(incident, proposal, validation)
    log_event({"stage": "approval", "incident_id": incident.incident_id, "approved": approved})
    if not approved:
        print("User declined or remediation was blocked.")
        return

    execution = run_script(proposal.powershell)
    log_event({
        "stage": "executed",
        "incident_id": incident.incident_id,
        "returncode": execution.returncode,
        "stdout": execution.stdout[-2000:],
        "stderr": execution.stderr[-2000:],
    })

    if proposal.verification_powershell.strip():
        verification = validate_script(proposal.verification_powershell, incident)
        log_event({
            "stage": "verification_validated",
            "incident_id": incident.incident_id,
            "validation": verification.model_dump(),
        })
        if verification.allowed:
            verified = run_script(proposal.verification_powershell)
            print(f"Verification return code: {verified.returncode}")
            if verified.stdout.strip():
                print(f"Verification output: {verified.stdout.strip()[:500]}")
            log_event({
                "stage": "verified",
                "incident_id": incident.incident_id,
                "returncode": verified.returncode,
                "stdout": verified.stdout[-2000:],
                "stderr": verified.stderr[-2000:],
            })
        else:
            print("AI verification command was blocked by local policy; remediation result was not auto-verified.")

    print("Remediation attempt complete. See logs/agent.jsonl for the audit trail.")


def run_demo_lab():
    print("Windows AI Support Agent started in DEMO MODE.")
    print("Only the Windows symptoms are simulated; AI reasoning is live.")
    while True:
        code = choose_demo_scenario()
        if code is None:
            return
        incident = build_demo_incident(code)
        handle(incident)


def run_monitor():
    print("Windows AI Support Agent started. Press Ctrl+C to stop.")
    while True:
        for incident in next_incidents():
            handle(incident)
        wait()


def main():
    from . import TLS_TRUST_MODE, TLS_TRUST_ERROR
    print(f"TLS trust: {TLS_TRUST_MODE}")
    if TLS_TRUST_ERROR:
        print(f"TLS trust warning: {TLS_TRUST_ERROR}")
    if DEMO_MODE:
        launch_gui()
    else:
        run_monitor()


if __name__ == "__main__":
    main()
