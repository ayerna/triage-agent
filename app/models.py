"""
SQLAlchemy ORM models — one class per database table.
Slice 1: users + incidents only.
Slice 2 migration adds: triage_runs, decisions.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SeverityEnum(str, enum.Enum):
    Low = "Low"
    Medium = "Medium"
    High = "High"
    Critical = "Critical"


class StatusEnum(str, enum.Enum):
    Open = "Open"
    Triaged = "Triaged"
    Approved = "Approved"
    Rejected = "Rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    incidents: Mapped[list["Incident"]] = relationship("Incident", back_populates="creator")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    severity: Mapped[SeverityEnum | None] = mapped_column(
        Enum(SeverityEnum, name="severity_enum"), nullable=True
    )
    status: Mapped[StatusEnum] = mapped_column(
        Enum(StatusEnum, name="status_enum"),
        nullable=False,
        default=StatusEnum.Open,
        server_default="Open",
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    creator: Mapped["User"] = relationship("User", back_populates="incidents")
    triage_runs: Mapped[list["TriageRun"]] = relationship(
        "TriageRun", back_populates="incident", order_by="TriageRun.created_at"
    )


class TriageRun(Base):
    """Added in Slice 2 — model defined here so SQLAlchemy sees it on import."""
    __tablename__ = "triage_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    proposal_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_check_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    critic_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    incident: Mapped["Incident"] = relationship("Incident", back_populates="triage_runs")
    decisions: Mapped[list["Decision"]] = relationship("Decision", back_populates="triage_run")


class Decision(Base):
    """Added in Slice 2 — model defined here so SQLAlchemy sees it on import."""
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    triage_run_id: Mapped[int] = mapped_column(ForeignKey("triage_runs.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)  # Approved / Rejected
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    triage_run: Mapped["TriageRun"] = relationship("TriageRun", back_populates="decisions")
    decider: Mapped["User"] = relationship("User")
