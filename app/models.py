from typing import Literal
from pydantic import BaseModel, Field

Risk = Literal["diagnostic", "safe_user_action", "requires_admin", "prohibited", "unknown"]


class Incident(BaseModel):
    incident_id: str
    kind: str
    summary: str
    details: str = ""
    process_name: str | None = None
    process_id: int | None = None


class Proposal(BaseModel):
    problem: str
    user_explanation: str
    impact: str
    powershell: str
    verification_powershell: str = ""
    risk: Risk = "unknown"
    requires_admin: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ValidationResult(BaseModel):
    allowed: bool
    reasons: list[str]
    commands: list[str] = []
