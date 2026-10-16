import tkinter as tk
from tkinter import messagebox
from .models import Incident, Proposal, ValidationResult


def _dialog_root() -> tk.Tk:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    # Ensure Windows has a chance to bring the hidden owner to the foreground before
    # the modal message box is created.
    root.update_idletasks()
    root.lift()
    root.focus_force()
    return root


def ask_for_approval(incident: Incident, proposal: Proposal, validation: ValidationResult) -> bool:
    root = _dialog_root()
    try:
        if not validation.allowed:
            reasons = "\n".join(f"• {reason}" for reason in validation.reasons[:6])
            if len(validation.reasons) > 6:
                reasons += "\n• Additional safety checks also failed."
            messagebox.showwarning(
                "Windows AI Support Agent — Fix Blocked",
                "I found a possible issue, but the AI-generated fix did not pass the local safety checks.\n\n"
                f"What I found:\n{proposal.user_explanation}\n\n"
                f"Why the fix was blocked:\n{reasons or 'The command did not meet local policy.'}\n\n"
                "Nothing was executed. This issue should be handled manually or escalated to IT.",
                parent=root,
            )
            return False

        text = (
            "I found a possible Windows problem.\n\n"
            f"What I found:\n{proposal.user_explanation}\n\n"
            f"What I can try:\n{proposal.impact}\n\n"
            "The proposed PowerShell command passed the local safety checks.\n"
            "Would you like me to try this fix now?"
        )
        return messagebox.askyesno(
            "Windows AI Support Agent — Approval Required",
            text,
            parent=root,
        )
    finally:
        root.destroy()
