"""
Pydantic v2 schemas — the shapes of request bodies and response bodies.
Keeping them separate from ORM models keeps the API contract stable
even if the database schema changes.
"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, field_validator


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Users ─────────────────────────────────────────────────────────────────────

class UserOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    email: str


# ── Incidents ─────────────────────────────────────────────────────────────────

class IncidentCreate(BaseModel):
    title: str
    description: str

    @field_validator("title", "description")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class IncidentOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    description: str
    category: str | None = None
    severity: str | None
    status: str
    created_by: int
    created_at: datetime


class IncidentListItem(BaseModel):
    """Slim view used in GET /incidents — includes latest triage info."""
    model_config = {"from_attributes": True}

    id: int
    title: str
    category: str | None = None
    severity: str | None
    status: str
    created_at: datetime
    latest_triage_id: int | None = None


# ── Triage ────────────────────────────────────────────────────────────────────

class TriageRunOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    incident_id: int
    proposal_json: str | None
    steps_json: str | None
    rule_check_json: str | None
    critic_json: str | None
    created_at: datetime


# ── Decisions ─────────────────────────────────────────────────────────────────

class DecisionCreate(BaseModel):
    decision: str
    note: str | None = None

    @field_validator("decision")
    @classmethod
    def valid_decision(cls, v: str) -> str:
        if v not in ("Approved", "Rejected"):
            raise ValueError("decision must be 'Approved' or 'Rejected'")
        return v


class DecisionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    triage_run_id: int
    decision: str
    note: str | None
    decided_by: int
    decided_at: datetime
