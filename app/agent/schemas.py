"""
Pydantic schemas for the Incident Triage Agent.
Defines the exact shape of agent outputs, deterministic rule check results,
and critic evaluations.
"""
from typing import Any, Literal
from pydantic import BaseModel, Field

# Allowed categories and severities per project spec
AllowedCategory = Literal["Hardware", "Software", "Network", "Safety", "Other"]
AllowedSeverity = Literal["Low", "Medium", "High", "Critical"]


class TriageProposal(BaseModel):
    severity: AllowedSeverity
    category: AllowedCategory
    summary: str = Field(..., min_length=5)
    recommended_action: str = Field(..., min_length=5)
    confidence: float = Field(..., ge=0.0, le=1.0)


class RuleCheckResult(BaseModel):
    passed: bool
    original_severity: str
    applied_severity: str
    forced_by_rule: bool
    rule_reason: str | None = None
    category_valid: bool = True
    severity_valid: bool = True


class CriticResult(BaseModel):
    verdict: Literal["ok", "flagged"]
    contradictions: list[str] = Field(default_factory=list)


class AgentStep(BaseModel):
    step: str  # plan, act, draft, check, rule_check, critic
    details: dict[str, Any]


class TriageRunResult(BaseModel):
    proposal: TriageProposal
    steps: list[AgentStep]
    rule_check: RuleCheckResult
    critic: CriticResult
