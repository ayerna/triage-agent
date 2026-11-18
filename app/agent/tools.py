"""
Agent tools reading from PostgreSQL.
Per spec:
1. search_past_incidents: text search over past incidents, returns id + title + severity + status
2. get_incident: reads details of a specific past incident match
"""
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Incident


async def search_past_incidents(
    db: AsyncSession,
    query: str,
    exclude_id: int | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    Search past incidents in PostgreSQL by matching query words against title or description.
    Returns id, title, severity, and status.
    """
    words = [w.strip() for w in query.split() if len(w.strip()) > 2]
    conditions = []
    for word in words:
        pattern = f"%{word}%"
        conditions.append(Incident.title.ilike(pattern))
        conditions.append(Incident.description.ilike(pattern))

    stmt = select(Incident)
    if conditions:
        stmt = stmt.where(or_(*conditions))
    if exclude_id is not None:
        stmt = stmt.where(Incident.id != exclude_id)

    stmt = stmt.order_by(Incident.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    incidents = result.scalars().all()

    return [
        {
            "id": inc.id,
            "title": inc.title,
            "severity": inc.severity.value if inc.severity else None,
            "status": inc.status.value,
        }
        for inc in incidents
    ]


async def get_incident(db: AsyncSession, incident_id: int) -> dict | None:
    """Fetch full details of an incident by ID."""
    stmt = select(Incident).where(Incident.id == incident_id)
    result = await db.execute(stmt)
    inc = result.scalar_one_or_none()
    if inc is None:
        return None

    return {
        "id": inc.id,
        "title": inc.title,
        "description": inc.description,
        "severity": inc.severity.value if inc.severity else None,
        "status": inc.status.value,
        "category": inc.category,
        "created_at": inc.created_at.isoformat() if inc.created_at else None,
    }
