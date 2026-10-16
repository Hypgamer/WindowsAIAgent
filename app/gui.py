from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk

from . import config
from .chatgpt_auth import has_profile, load_profile, sign_in

from .ai_agent import propose
from .demo_scenarios import SCENARIOS, build_demo_incident
from .driver_advisor import check_driver_online, collect_installed_drivers, collect_system_profile
from .logger import log_event
from .powershell import run_script, validate_script



class TkDispatch:
    """Thread-safe handoff from worker threads to Tk's main thread."""
    def __init__(self, root: tk.Tk, interval_ms: int = 50):
        self.root = root
        self.interval_ms = interval_ms
        self.events = queue.Queue()
        self.running = True
        # This first after() call is made on the Tk/main thread. From here on,
        # _drain reschedules itself from that same thread.
        self.root.after(self.interval_ms, self._drain)

    def post(self, callback, *args):
        self.events.put((callback, args))

    def _drain(self):
        if not self.running:
            return
        try:
            while True:
                callback, args = self.events.get_nowait()
                try:
                    callback(*args)
                except Exception as exc:
                    print(f"GUI callback error: {exc}")
        except queue.Empty:
            pass
        try:
            self.root.after(self.interval_ms, self._drain)
        except tk.TclError:
            self.running = False

    def stop(self):
        self.running = False



class ProviderSetupDialog:
    def __init__(self, root: tk.Tk, dispatcher: TkDispatch):
        self.root = root
        self.dispatcher = dispatcher
        self.result = False
        self.window = tk.Toplevel(root)
        self.window.title("Windows AI Support Agent — AI Connection")
        self.window.geometry("620x470")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        # The main root is intentionally hidden during first-run setup.
        # Do not make this dialog transient to a withdrawn owner on Windows,
        # or the chooser may never become visible.
        self.window.update_idletasks()
        self.window.lift()
        self.window.attributes("-topmost", True)
        self.window.after(500, lambda: self.window.attributes("-topmost", False))
        self.window.focus_force()
        self.window.grab_set()

        frame = ttk.Frame(self.window, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Choose how to connect to AI", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="You can use an eligible ChatGPT account or your own OpenAI API key.",
            wraplength=560,
        ).pack(anchor="w", pady=(4, 18))

        self.provider = tk.StringVar(value="chatgpt")
        ttk.Radiobutton(frame, text="Sign in with ChatGPT", variable=self.provider, value="chatgpt", command=self._refresh).pack(anchor="w", pady=5)
        ttk.Label(frame, text="Opens your browser for OpenAI's supported sign-in flow. No API key is required when plan usage is available.", wraplength=535).pack(anchor="w", padx=(24,0))
        ttk.Radiobutton(frame, text="OpenAI API Key", variable=self.provider, value="api", command=self._refresh).pack(anchor="w", pady=(18,5))
        ttk.Label(frame, text="Uses an API key stored in this app's local configuration file.", wraplength=535).pack(anchor="w", padx=(24,0))

        keyrow = ttk.Frame(frame)
        keyrow.pack(fill="x", padx=(24,0), pady=(8,0))
        ttk.Label(keyrow, text="API key:").pack(side="left")
        self.key_var = tk.StringVar(value=config.OPENAI_API_KEY)
        self.key_entry = ttk.Entry(keyrow, textvariable=self.key_var, show="•", width=48)
        self.key_entry.pack(side="left", padx=(8,0), fill="x", expand=True)

        self.status_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.status_var, wraplength=560).pack(anchor="w", pady=(18,8))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="right")
        self.continue_btn = ttk.Button(buttons, text="Continue", command=self._continue)
        self.continue_btn.pack(side="right", padx=(0,8))
        self._refresh()

    def _refresh(self):
        self.key_entry.configure(state="normal" if self.provider.get() == "api" else "disabled")

    def _set_busy(self, busy: bool):
        self.continue_btn.configure(state="disabled" if busy else "normal")
        self.key_entry.configure(state="disabled" if busy or self.provider.get() != "api" else "normal")

    def _continue(self):
        if self.provider.get() == "api":
            key = self.key_var.get().strip()
            if not key:
                messagebox.showerror("API Key Required", "Enter your OpenAI API key.", parent=self.window)
                return
            config.update_env({"AI_PROVIDER": "api", "OPENAI_API_KEY": key, "SETUP_COMPLETE": "true"})
            self.result = True
            self.window.destroy()
            return

        self._set_busy(True)
        self.status_var.set("Opening your browser for ChatGPT sign-in...")
        threading.Thread(target=self._chatgpt_worker, daemon=True).start()

    def _chatgpt_worker(self):
        try:
            profile = sign_in(progress=self._chatgpt_progress_from_worker)
        except Exception as exc:
            self.dispatcher.post(self._chatgpt_failed, str(exc))
            return
        self.dispatcher.post(self._chatgpt_complete, profile)


    def _chatgpt_progress_from_worker(self, message: str):
        # OAuth runs off the Tk thread. Marshal status updates back to Tk.
        try:
            self.dispatcher.post(self.status_var.set, message)
        except Exception:
            pass

    def _chatgpt_failed(self, error: str):
        self._set_busy(False)
        self.status_var.set("ChatGPT sign-in was not completed.")
        messagebox.showerror("ChatGPT Sign-in Failed", error, parent=self.window)

    def _chatgpt_complete(self, profile: dict):
        config.update_env({"AI_PROVIDER": "chatgpt", "SETUP_COMPLETE": "true"})
        email = profile.get("email") or "ChatGPT account"
        self.status_var.set(f"Connected as {email}.")
        self.result = True
        self.window.destroy()

    def _cancel(self):
        self.result = False
        self.window.destroy()


def _provider_is_ready() -> bool:
    config.reload_settings()
    if config.AI_PROVIDER == "api":
        return bool(config.OPENAI_API_KEY)
    if config.AI_PROVIDER == "chatgpt":
        return has_profile()
    if config.AI_PROVIDER == "auto":
        return bool(config.OPENAI_API_KEY) or has_profile()
    return False


def ensure_provider_setup(root: tk.Tk, dispatcher: TkDispatch) -> bool:
    # Always reload from the application-owned .env path before making the first-run decision.
    config.reload_settings()
    print(f"Config file: {config.ENV_PATH}")
    print(f"Setup complete: {config.SETUP_COMPLETE}; AI provider: {config.AI_PROVIDER}")

    # Only skip setup when the project .env explicitly says setup is complete
    # and the selected provider has usable credentials.
    if config.SETUP_COMPLETE and _provider_is_ready() and config.AI_PROVIDER != "auto":
        return True
    dialog = ProviderSetupDialog(root, dispatcher)
    root.wait_window(dialog.window)
    return dialog.result

class SupportAgentGUI:
    def __init__(self, root: tk.Tk, dispatcher: TkDispatch):
        self.root = root
        self.dispatcher = dispatcher
        self.root.title("Windows AI Support Agent — Demo Lab")
        self.root.geometry("1040x760")
        self.root.minsize(900, 650)

        self.incident = None
        self.proposal_result = None
        self.validation = None
        self.driver_advice = None
        self.busy = False

        self._build_ui()
        self._set_status("Ready — choose a demo scenario.")

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Windows AI Support Agent", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="Demo Lab — synthetic Windows symptoms, live AI reasoning, local safety validation",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(2, 10))
        config.reload_settings()
        provider_name = "ChatGPT sign-in" if config.AI_PROVIDER == "chatgpt" else "OpenAI API key"
        self.provider_var = tk.StringVar(value=f"AI connection: {provider_name}  |  Config: {config.ENV_PATH}")
        ttk.Label(header, textvariable=self.provider_var, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 8))

        self.status_var = tk.StringVar()
        status = ttk.Label(outer, textvariable=self.status_var, relief="sunken", anchor="w", padding=(8, 5))
        status.pack(fill="x", pady=(0, 12))

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, padding=(0, 0, 10, 0))
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=3)

        ttk.Label(left, text="Demo scenarios", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        self.scenario_buttons = []
        for code, (label, _kind) in SCENARIOS.items():
            button = ttk.Button(left, text=f"{code}. {label}", command=lambda c=code: self.start_scenario(c))
            button.pack(fill="x", pady=4)
            self.scenario_buttons.append(button)

        ttk.Separator(left).pack(fill="x", pady=14)
        ttk.Label(
            left,
            text=(
                "Demo mode simulates only the diagnostic symptom.\n\n"
                "The diagnosis and remediation proposal come from the live AI provider every time."
            ),
            wraplength=220,
            justify="left",
        ).pack(anchor="w")

        self.approve_button = ttk.Button(left, text="Approve & Run Fix", command=self.approve_and_run, state="disabled")
        self.approve_button.pack(fill="x", pady=(18, 4))
        self.decline_button = ttk.Button(left, text="Decline Fix", command=self.decline_fix, state="disabled")
        self.decline_button.pack(fill="x", pady=4)
        self.driver_button = ttk.Button(left, text="Check Driver", command=self.check_driver, state="disabled")
        self.driver_button.pack(fill="x", pady=(14, 4))
        self.open_driver_source_button = ttk.Button(left, text="Open Official Driver Source", command=self.open_driver_source, state="disabled")
        self.open_driver_source_button.pack(fill="x", pady=4)
        self.reset_button = ttk.Button(left, text="Clear Results", command=self.clear_results)
        self.reset_button.pack(fill="x", pady=4)
        self.connection_button = ttk.Button(left, text="Change AI Connection", command=self.change_ai_connection)
        self.connection_button.pack(fill="x", pady=(14, 4))

        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)

        summary_frame = ttk.Frame(notebook, padding=12)
        tech_frame = ttk.Frame(notebook, padding=12)
        audit_frame = ttk.Frame(notebook, padding=12)
        notebook.add(summary_frame, text="Summary")
        notebook.add(tech_frame, text="Technical Details")
        notebook.add(audit_frame, text="Run Log")

        self.summary_text = tk.Text(summary_frame, wrap="word", font=("Segoe UI", 11), state="disabled")
        self.summary_text.pack(fill="both", expand=True)

        self.tech_text = tk.Text(tech_frame, wrap="word", font=("Consolas", 10), state="disabled")
        self.tech_text.pack(fill="both", expand=True)

        self.audit_text = tk.Text(audit_frame, wrap="word", font=("Consolas", 9), state="disabled")
        self.audit_text.pack(fill="both", expand=True)

    def _set_status(self, text: str):
        self.status_var.set(text)
        self.root.update_idletasks()

    def _set_text(self, widget: tk.Text, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _append_audit(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.audit_text.configure(state="normal")
        self.audit_text.insert("end", f"[{timestamp}] {text}\n")
        self.audit_text.see("end")
        self.audit_text.configure(state="disabled")

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in self.scenario_buttons:
            button.configure(state=state)
        if busy:
            self.approve_button.configure(state="disabled")
            self.decline_button.configure(state="disabled")
            self.driver_button.configure(state="disabled")
            self.open_driver_source_button.configure(state="disabled")

    def change_ai_connection(self):
        if self.busy:
            return
        dialog = ProviderSetupDialog(self.root, self.dispatcher)
        self.root.wait_window(dialog.window)
        if dialog.result:
            config.reload_settings()
            provider_name = "ChatGPT sign-in" if config.AI_PROVIDER == "chatgpt" else "OpenAI API key"
            self.provider_var.set(f"AI connection: {provider_name}  |  Config: {config.ENV_PATH}")
            self._append_audit(f"AI connection changed to {provider_name}.")
            self._set_status(f"AI connection changed to {provider_name}.")

    def clear_results(self):
        if self.busy:
            return
        self.incident = None
        self.proposal_result = None
        self.validation = None
        self.driver_advice = None
        self.approve_button.configure(state="disabled")
        self.decline_button.configure(state="disabled")
        self.driver_button.configure(state="disabled")
        self.open_driver_source_button.configure(state="disabled")
        self._set_text(self.summary_text, "")
        self._set_text(self.tech_text, "")
        self._set_text(self.audit_text, "")
        self._set_status("Ready — choose a demo scenario.")

    def start_scenario(self, code: str):
        if self.busy:
            return

        try:
            self.incident = build_demo_incident(code)
        except Exception as exc:
            messagebox.showerror("Demo Error", f"Could not create the demo scenario:\n\n{exc}", parent=self.root)
            return

        self.proposal_result = None
        self.validation = None
        self.driver_advice = None
        self.approve_button.configure(state="disabled")
        self.decline_button.configure(state="disabled")
        self._set_text(
            self.summary_text,
            f"Detected problem\n\n{self.incident.summary}\n\nContacting the AI provider for a live recommendation...",
        )
        self._set_text(self.tech_text, f"INCIDENT\n{self.incident.model_dump_json(indent=2)}\n")
        self._append_audit(f"Scenario started: {self.incident.summary}")
        log_event({"stage": "detected", "incident": self.incident.model_dump()})

        self._set_status("Contacting AI provider — no remediation is hard-coded...")
        self._set_busy(True)
        threading.Thread(target=self._analyze_worker, daemon=True).start()

    def _analyze_worker(self):
        try:
            result = propose(self.incident)
        except Exception as exc:
            self.dispatcher.post(self._analysis_failed, str(exc))
            return

        log_event({
            "stage": "proposed",
            "incident_id": self.incident.incident_id,
            "provider": result.provider,
            "model": result.model,
            "raw_ai_response": result.raw_response,
            "proposal": result.proposal.model_dump(),
        })

        proposal = result.proposal
        if proposal.requires_admin or proposal.risk in {"requires_admin", "prohibited", "unknown"}:
            self.dispatcher.post(self._analysis_escalated, result)
            return

        try:
            validation = validate_script(proposal.powershell, self.incident)
        except Exception as exc:
            self.dispatcher.post(self._validation_failed, result, str(exc))
            return

        log_event({
            "stage": "validated",
            "incident_id": self.incident.incident_id,
            "validation": validation.model_dump(),
        })
        self.dispatcher.post(self._analysis_complete, result, validation)

    def _analysis_failed(self, error: str):
        self._set_busy(False)
        self._set_status("AI request failed — nothing was executed.")
        self._append_audit(f"AI ERROR: {error}")
        log_event({"stage": "ai_error", "incident_id": self.incident.incident_id, "error": error})
        self._set_text(
            self.summary_text,
            "AI analysis failed.\n\nNo fallback response was used and nothing was executed.\n\n"
            f"Error:\n{error}",
        )

    def _analysis_escalated(self, result):
        self.proposal_result = result
        self._set_busy(False)
        proposal = result.proposal
        self._set_status("AI recommends escalation — no remediation will run.")
        self._append_audit(f"AI response: provider={result.provider}, model={result.model}, risk={proposal.risk}")
        log_event({"stage": "escalated", "incident_id": self.incident.incident_id, "reason": proposal.risk})
        self._show_proposal(result, None, escalated=True)
        if self.incident and any(k in self.incident.kind for k in ("audio", "display", "peripheral")):
            self.driver_button.configure(state="normal")

    def _validation_failed(self, result, error: str):
        self.proposal_result = result
        self._set_busy(False)
        self._set_status("Safety validation failed — nothing was executed.")
        self._append_audit(f"VALIDATION ERROR: {error}")
        log_event({"stage": "validation_error", "incident_id": self.incident.incident_id, "error": error})
        self._show_proposal(result, None, validation_error=error)

    def _analysis_complete(self, result, validation):
        self.proposal_result = result
        self.validation = validation
        self._set_busy(False)
        self._append_audit(f"AI response received: provider={result.provider}, model={result.model}")
        self._append_audit(f"Safety validation: {'PASS' if validation.allowed else 'BLOCKED'}")
        self._show_proposal(result, validation)

        if validation.allowed and result.proposal.powershell.strip():
            self._set_status("Recommendation ready — review it and approve only if you want the fix applied.")
            self.approve_button.configure(state="normal")
            self.decline_button.configure(state="normal")
        else:
            self._set_status("AI recommendation was blocked or contains no executable remediation.")
            self.decline_button.configure(state="normal")

        if self.incident and any(k in self.incident.kind for k in ("audio", "display", "peripheral")):
            self.driver_button.configure(state="normal")

    def _show_proposal(self, result, validation, escalated=False, validation_error=None):
        p = result.proposal
        validation_label = "NOT RUN"
        validation_reasons = ""
        if validation is not None:
            validation_label = "PASSED" if validation.allowed else "BLOCKED"
            if validation.reasons:
                validation_reasons = "\n" + "\n".join(f"• {r}" for r in validation.reasons)
        elif validation_error:
            validation_label = "ERROR"
            validation_reasons = f"\n• {validation_error}"

        if escalated:
            outcome = "The AI determined that this issue should be escalated. No command will be executed."
        elif validation is not None and validation.allowed:
            outcome = "The proposed remediation passed local safety checks. It will NOT run unless you approve it."
        else:
            outcome = "The proposed remediation will not run."

        summary = (
            f"AI ANALYSIS\n\n"
            f"Problem\n{p.problem}\n\n"
            f"What this means\n{p.user_explanation}\n\n"
            f"Proposed action / impact\n{p.impact}\n\n"
            f"Confidence\n{p.confidence:.0%}\n\n"
            f"Safety validation\n{validation_label}{validation_reasons}\n\n"
            f"Result\n{outcome}\n"
        )
        self._set_text(self.summary_text, summary)

        tech = (
            f"PROVIDER\n{result.provider}\n\n"
            f"MODEL\n{result.model}\n\n"
            f"INCIDENT\n{self.incident.model_dump_json(indent=2)}\n\n"
            f"AI-GENERATED POWERSHELL\n{p.powershell or '[none]'}\n\n"
            f"AI-GENERATED VERIFICATION\n{p.verification_powershell or '[none]'}\n\n"
            f"RISK\n{p.risk}\n\n"
            f"REQUIRES ADMIN\n{p.requires_admin}\n"
        )
        if validation is not None:
            tech += f"\nVALIDATOR COMMANDS\n{json.dumps(validation.commands, indent=2)}\n"
            tech += f"\nVALIDATOR REASONS\n{json.dumps(validation.reasons, indent=2)}\n"
        self._set_text(self.tech_text, tech)

    def decline_fix(self):
        if not self.incident:
            return
        self.approve_button.configure(state="disabled")
        self.decline_button.configure(state="disabled")
        self._set_status("Fix declined — nothing was executed.")
        self._append_audit("User declined remediation. Nothing executed.")
        log_event({"stage": "approval", "incident_id": self.incident.incident_id, "approved": False})

    def check_driver(self):
        if self.busy or not self.incident:
            return
        if not any(k in self.incident.kind for k in ("audio", "display", "peripheral")):
            messagebox.showinfo("Driver Check", "Driver advisory is currently available for Audio and Display/USB scenarios.", parent=self.root)
            return
        self.driver_button.configure(state="disabled")
        self.open_driver_source_button.configure(state="disabled")
        self._set_busy(True)
        self._set_status("Reading installed drivers and searching approved official sources...")
        self._append_audit("Driver advisory started: collecting read-only local driver inventory.")
        threading.Thread(target=self._driver_worker, daemon=True).start()

    def _driver_worker(self):
        try:
            profile = collect_system_profile()
            drivers = collect_installed_drivers(self.incident)
            log_event({
                "stage": "driver_inventory",
                "incident_id": self.incident.incident_id,
                "system_profile": profile.model_dump(),
                "drivers": [d.model_dump() for d in drivers],
            })
            advice = check_driver_online(self.incident, drivers, profile)
            log_event({
                "stage": "driver_advisory",
                "incident_id": self.incident.incident_id,
                "provider": advice.provider,
                "model": advice.model,
                "raw_ai_response": advice.raw_response,
                "results": [r.model_dump() for r in advice.results],
                "overall_summary": advice.overall_summary,
            })
        except Exception as exc:
            self.dispatcher.post(self._driver_failed, str(exc))
            return
        self.dispatcher.post(self._driver_complete, advice)

    def _driver_failed(self, error: str):
        self._set_busy(False)
        if self.incident and any(k in self.incident.kind for k in ("audio", "display", "peripheral")):
            self.driver_button.configure(state="normal")
        self._set_status("Driver advisory could not be completed. No driver changes were made.")
        self._append_audit(f"DRIVER CHECK ERROR: {error}")
        log_event({"stage": "driver_advisory_error", "incident_id": self.incident.incident_id, "error": error})
        messagebox.showerror(
            "Driver Check Failed",
            f"The driver advisory could not be completed.\n\n{error}\n\nNo driver was downloaded or installed.",
            parent=self.root,
        )

    def _driver_complete(self, advice):
        self.driver_advice = advice
        self._set_busy(False)
        self.driver_button.configure(state="normal")
        urls = [r.source_url for r in advice.results if r.source_url]
        if urls:
            self.open_driver_source_button.configure(state="normal")
        self._set_status("Driver advisory complete — no driver was downloaded or installed.")
        self._append_audit(
            f"Driver advisory received: provider={advice.provider}, model={advice.model}, "
            f"packages_checked={len(advice.results)}"
        )

        lines = ["\n\nDRIVER ADVISORY\n"]
        p = advice.system_profile
        lines.extend([
            f"Computer: {' '.join(x for x in [p.system_manufacturer, p.system_model, p.system_sku] if x) or 'Unknown'}",
            f"Form factor: {p.form_factor}",
        ])
        if p.form_factor != "laptop":
            lines.append(
                f"Motherboard: {' '.join(x for x in [p.motherboard_manufacturer, p.motherboard_product, p.motherboard_version] if x) or 'Unknown'}"
            )
        lines.append(f"Lookup strategy: {p.lookup_strategy}\n")
        if advice.overall_summary:
            lines.append(advice.overall_summary + "\n")
        if not advice.results:
            lines.append("The AI did not return any per-driver comparison results.\n")

        for index, r in enumerate(advice.results, 1):
            newer = "Unknown" if r.newer_available is None else ("Yes" if r.newer_available else "No")
            lines.extend([
                f"\n[{index}] {r.device_name}",
                f"INF/package: {r.inf_name or 'Unknown'}",
                f"Installed version: {r.installed_version or 'Unknown'}",
                f"Installed date: {r.installed_date or 'Unknown'}",
                f"Latest applicable version found: {r.latest_version or 'Not established'}",
                f"Latest date: {r.latest_date or 'Not established'}",
                f"Newer driver available: {newer}",
                f"Hardware match: {r.hardware_match or 'Unknown'}",
                f"Official source: {r.source_name or 'Not established'}",
                f"Source URL: {r.source_url or 'Not established'}",
                f"Confidence: {r.confidence:.2f}" if r.confidence is not None else "Confidence: Unknown",
                f"Recommendation: {r.recommendation}",
            ])
            if r.notes:
                lines.append(f"Notes: {r.notes}")
            if r.source_url:
                self._append_audit(f"Driver source [{index}]: {r.source_name or 'official source'} {r.source_url}")

        lines.append("\nThis feature is advisory only. It did not download, install, remove, or change any driver.")
        previous = self.summary_text.get("1.0", "end").strip()
        self._set_text(self.summary_text, previous + "\n".join(lines))

        tech = self.tech_text.get("1.0", "end").strip()
        profile_json = json.dumps(advice.system_profile.model_dump(), indent=2)
        inventory_json = json.dumps([d.model_dump() for d in advice.installed], indent=2)
        self._set_text(
            self.tech_text,
            tech
            + "\n\nCOMPUTER / MOTHERBOARD IDENTITY (READ ONLY)\n"
            + profile_json
            + "\n\nDRIVER INVENTORY (READ ONLY — UNIQUE PACKAGES)\n"
            + inventory_json
            + "\n\nDRIVER AI RESPONSE\n"
            + advice.raw_response,
        )

    def open_driver_source(self):
        if not self.driver_advice:
            return
        urls = []
        for r in self.driver_advice.results:
            if r.source_url and r.source_url not in urls:
                urls.append(r.source_url)
        if not urls:
            return
        for url in urls:
            webbrowser.open(url)
        self._append_audit(f"Opened {len(urls)} official driver source page(s) in the default browser.")

    def approve_and_run(self):
        if self.busy or not self.incident or not self.proposal_result or not self.validation:
            return
        if not self.validation.allowed:
            messagebox.showwarning("Fix Blocked", "This remediation did not pass the local safety checks.", parent=self.root)
            return

        p = self.proposal_result.proposal
        confirmed = messagebox.askyesno(
            "Approval Required",
            f"What I found:\n{p.user_explanation}\n\n"
            f"What I can try:\n{p.impact}\n\n"
            "The generated PowerShell passed the local safety checks.\n\n"
            "Do you want me to run this fix now?",
            parent=self.root,
        )
        if not confirmed:
            self.decline_fix()
            return

        log_event({"stage": "approval", "incident_id": self.incident.incident_id, "approved": True})
        self._append_audit("User APPROVED remediation.")
        self.approve_button.configure(state="disabled")
        self.decline_button.configure(state="disabled")
        self._set_busy(True)
        self._set_status("Running approved remediation...")
        threading.Thread(target=self._execute_worker, daemon=True).start()

    def _execute_worker(self):
        p = self.proposal_result.proposal
        try:
            execution = run_script(p.powershell)
            log_event({
                "stage": "executed",
                "incident_id": self.incident.incident_id,
                "returncode": execution.returncode,
                "stdout": execution.stdout[-2000:],
                "stderr": execution.stderr[-2000:],
            })
        except Exception as exc:
            self.dispatcher.post(self._execution_failed, str(exc))
            return

        verification = None
        verification_run = None
        verification_error = None
        if p.verification_powershell.strip():
            try:
                verification = validate_script(p.verification_powershell, self.incident)
                log_event({
                    "stage": "verification_validated",
                    "incident_id": self.incident.incident_id,
                    "validation": verification.model_dump(),
                })
                if verification.allowed:
                    verification_run = run_script(p.verification_powershell)
                    log_event({
                        "stage": "verified",
                        "incident_id": self.incident.incident_id,
                        "returncode": verification_run.returncode,
                        "stdout": verification_run.stdout[-2000:],
                        "stderr": verification_run.stderr[-2000:],
                    })
            except Exception as exc:
                verification_error = str(exc)

        self.dispatcher.post(self._execution_complete, execution, verification, verification_run, verification_error)

    def _execution_failed(self, error: str):
        self._set_busy(False)
        self._set_status("Remediation failed to execute.")
        self._append_audit(f"EXECUTION ERROR: {error}")
        messagebox.showerror("Remediation Failed", f"The approved remediation could not be executed.\n\n{error}", parent=self.root)

    def _execution_complete(self, execution, verification, verification_run, verification_error):
        self._set_busy(False)
        success = execution.returncode == 0
        self._append_audit(f"Remediation execution return code: {execution.returncode}")

        verify_text = "No verification command was proposed."
        if verification_error:
            verify_text = f"Verification could not be completed: {verification_error}"
        elif verification is not None and not verification.allowed:
            verify_text = "The AI-generated verification command was blocked by local safety policy."
        elif verification_run is not None:
            verify_text = f"Verification return code: {verification_run.returncode}"
            if verification_run.stdout.strip():
                verify_text += f"\nVerification output: {verification_run.stdout.strip()[:800]}"

        status_word = "completed successfully" if success else "returned an error"
        self._set_status(f"Remediation {status_word}.")
        self._append_audit(verify_text.replace("\n", " | "))

        previous = self.summary_text.get("1.0", "end").strip()
        result_summary = (
            f"{previous}\n\n"
            "EXECUTION RESULT\n\n"
            f"PowerShell return code: {execution.returncode}\n"
            f"{verify_text}\n\n"
            "The complete audit trail was also written to the app data logs folder."
        )
        self._set_text(self.summary_text, result_summary)

        messagebox.showinfo(
            "Remediation Result",
            f"The approved remediation {status_word}.\n\n{verify_text}",
            parent=self.root,
        )


def launch_gui():
    root = tk.Tk()
    root.title("Windows AI Support Agent")

    # Keep the owner alive and visible long enough for Windows to map first-run
    # dialogs reliably. Make it effectively invisible instead of withdrawing it.
    root.geometry("1x1+0+0")
    try:
        root.attributes("-alpha", 0.0)
    except tk.TclError:
        pass
    root.update_idletasks()
    dispatcher = TkDispatch(root)

    if not ensure_provider_setup(root, dispatcher):
        root.destroy()
        return

    try:
        root.attributes("-alpha", 1.0)
    except tk.TclError:
        pass
    root.geometry("1040x760")
    root.deiconify()
    root.lift()
    SupportAgentGUI(root, dispatcher)
    try:
        root.mainloop()
    finally:
        dispatcher.stop()
