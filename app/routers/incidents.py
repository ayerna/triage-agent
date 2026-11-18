"""
Incident CRUD endpoints — all protected by JWT.

GET  /incidents        — list with latest triage status (JOIN across tables)
POST /incidents        — create; 400 on blank title/description
GET  /incidents/{id}   — detail; 404 if missing
POST /incidents/{id}/triage    — placeholder in Slice 1 (implemented in Slice 2)
POST /incidents/{id}/decision  — placeholder in Slice 1 (implemented in Slice 2)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, outerjoin
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Decision, Incident, TriageRun, User
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
    """Return all incidents with latest triage_run id (via LEFT JOIN)."""
    # Subquery: latest triage run per incident
    result = await db.execute(select(Incident).order_by(Incident.created_at.desc()))
    incidents = result.scalars().all()

    # For each incident, get the latest triage_run id
    items = []
    for inc in incidents:
        tr_result = await db.execute(
            select(TriageRun.id)
            .where(TriageRun.incident_id == inc.id)
            .order_by(TriageRun.created_at.desc())
            .limit(1)
        )
        latest_tr_id = tr_result.scalar_one_or_none()
        items.append(
            IncidentListItem(
                id=inc.id,
                title=inc.title,
                severity=inc.severity.value if inc.severity else None,
                status=inc.status.value,
                created_at=inc.created_at,
                latest_triage_id=latest_tr_id,
            )
        )
    return items


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
        severity=incident.severity.value if incident.severity else None,
        status=incident.status.value,
        created_by=incident.created_by,
        created_at=incident.created_at,
    )


@router.post("/{incident_id}/triage", response_model=TriageRunOut, status_code=status.HTTP_201_CREATED)
async def triage_incident(
    incident_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Agent loop — implemented in Slice 2. Returns 501 in Slice 1."""
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    incident = result.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Triage agent not yet implemented (Slice 2)",
    )


@router.post("/{incident_id}/decision", response_model=DecisionOut, status_code=status.HTTP_201_CREATED)
async def record_decision(
    incident_id: int,
    body: DecisionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Decision endpoint — implemented in Slice 2. Returns 501 in Slice 1."""
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    incident = result.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Decision recording not yet implemented (Slice 2)",
    )
