"""
Incident CRUD and Agent endpoints — all protected by JWT.

GET  /incidents                — list with latest triage status (JOIN across tables)
POST /incidents                — create; 400 on blank title/description
GET  /incidents/{id}           — detail; 404 if missing
POST /incidents/{id}/triage    — runs agent loop (framework-free or langgraph)
POST /incidents/{id}/decision  — writes decision & updates status in ONE atomic transaction
"""
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import run_langgraph_triage_loop
from app.agent.loop import TriageError, run_triage_loop
from app.auth import get_current_user
from app.database import get_db
from app.models import Decision, Incident, SeverityEnum, StatusEnum, TriageRun, User
from app.schemas import (
    DecisionCreate,
    DecisionOut,
    IncidentCreate,
    IncidentListItem,
    IncidentOut,
    TriageRunOut,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("", response_model=list[IncidentListItem])
async def list_incidents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all incidents with latest triage_run id in ONE database JOIN query."""
    latest_runs = (
        select(
            TriageRun.id.label("triage_run_id"),
            TriageRun.incident_id,
            func.row_number()
            .over(
                partition_by=TriageRun.incident_id,
                order_by=TriageRun.created_at.desc(),
            )
            .label("rn"),
        )
        .subquery()
    )

    stmt = (
        select(Incident, latest_runs.c.triage_run_id)
        .outerjoin(
            latest_runs,
            (latest_runs.c.incident_id == Incident.id) & (latest_runs.c.rn == 1),
        )
        .order_by(Incident.created_at.desc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    return [
        IncidentListItem(
            id=inc.id,
            title=inc.title,
            category=inc.category,
            severity=inc.severity.value if inc.severity else None,
            status=inc.status.value,
            created_at=inc.created_at,
            latest_triage_id=triage_run_id,
        )
        for inc, triage_run_id in rows
    ]


@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
async def create_incident(
    body: IncidentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    incident = Incident(
        title=body.title,
        description=body.description,
        created_by=current_user.id,
    )
    db.add(incident)
    await db.commit()
    await db.refresh(incident)
    return IncidentOut(
        id=incident.id,
        title=incident.title,
        description=incident.description,
        category=incident.category,
        severity=incident.severity.value if incident.severity else None,
        status=incident.status.value,
        created_by=incident.created_by,
        created_at=incident.created_at,
    )


@router.get("/{incident_id}", response_model=IncidentOut)
async def get_incident(
    incident_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    incident = result.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return IncidentOut(
        id=incident.id,
        title=incident.title,
        description=incident.description,
        category=incident.category,
        severity=incident.severity.value if incident.severity else None,
        status=incident.status.value,
        created_by=incident.created_by,
        created_at=incident.created_at,
    )


@router.post("/{incident_id}/triage", response_model=TriageRunOut, status_code=status.HTTP_201_CREATED)
async def triage_incident(
    incident_id: int,
    engine: Literal["framework_free", "langgraph"] = Query(
        "framework_free",
        description="Choose triage execution engine: framework_free or langgraph",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Run the triage agent loop for this incident.
    Supports engine='framework_free' (default) or engine='langgraph'.
    Persists TriageRun with proposal, steps, rule checks, critic output.
    Updates incident status to Triaged.
    """
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    incident = result.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    try:
        if engine == "langgraph":
            triage_result = await run_langgraph_triage_loop(db=db, incident=incident)
        else:
            triage_result = await run_triage_loop(db=db, incident=incident)
    except TriageError as err:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(err))

    # Persist triage run record
    triage_run = TriageRun(
        incident_id=incident.id,
        proposal_json=triage_result.proposal.model_dump_json(),
        steps_json=json.dumps([s.model_dump() for s in triage_result.steps]),
        rule_check_json=triage_result.rule_check.model_dump_json(),
        critic_json=triage_result.critic.model_dump_json(),
    )
    db.add(triage_run)

    # Update incident severity, category, and status to Triaged
    incident.severity = SeverityEnum(triage_result.proposal.severity)
    incident.category = triage_result.proposal.category
    incident.status = StatusEnum.Triaged

    await db.commit()
    await db.refresh(triage_run)

    return TriageRunOut(
        id=triage_run.id,
        incident_id=triage_run.incident_id,
        proposal_json=triage_run.proposal_json,
        steps_json=triage_run.steps_json,
        rule_check_json=triage_run.rule_check_json,
        critic_json=triage_run.critic_json,
        created_at=triage_run.created_at,
    )


@router.post("/{incident_id}/decision", response_model=DecisionOut, status_code=status.HTTP_201_CREATED)
async def record_decision(
    incident_id: int,
    body: DecisionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Record human decision (Approved/Rejected) and update incident status
    in ONE atomic database transaction, rolling back both on any error.
    """
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    incident = result.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    # Get latest triage run for this incident
    tr_res = await db.execute(
        select(TriageRun)
        .where(TriageRun.incident_id == incident.id)
        .order_by(TriageRun.created_at.desc())
        .limit(1)
    )
    latest_run = tr_res.scalar_one_or_none()
    if latest_run is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot decide on an incident that has not been triaged",
        )

    # Atomic write: insert decision + update status in ONE transaction
    try:
        decision_rec = Decision(
            triage_run_id=latest_run.id,
            decision=body.decision,
            note=body.note,
            decided_by=current_user.id,
        )
        db.add(decision_rec)

        # Update incident status
        new_status = StatusEnum.Approved if body.decision == "Approved" else StatusEnum.Rejected
        incident.status = new_status

        await db.commit()
        await db.refresh(decision_rec)
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database transaction failed and was rolled back: {e}",
        )

    return DecisionOut(
        id=decision_rec.id,
        triage_run_id=decision_rec.triage_run_id,
        decision=decision_rec.decision,
        note=decision_rec.note,
        decided_by=decision_rec.decided_by,
        decided_at=decision_rec.decided_at,
    )
