"""
Framework-free agent loop for Incident Triage.
Executes the 7 steps:
1. Plan
2. Act (tools: search_past_incidents, get_incident)
3. Draft (JSON structured proposal)
4. Check (Pydantic validation, 1 retry on invalid JSON, fail on second)
5. Rule check (deterministic safety rules)
6. Critic pass (verification against evidence)
7. Stop (return structured triage run result, no auto-approval)
"""
import json
from typing import Any, Callable
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.rules import deterministic_rule_check
from app.agent.schemas import (
    AgentStep,
    CriticResult,
    RuleCheckResult,
    TriageProposal,
    TriageRunResult,
)
from app.agent.tools import get_incident, search_past_incidents
from app.models import Incident


class TriageError(Exception):
    """Raised when triage agent cannot produce valid output after retry."""
    pass


# ── Stub Decider for Slice 2 ─────────────────────────────────────────────────

def stub_decider(
    incident: dict,
    past_evidence: list[dict],
    retry_error: str | None = None,
) -> str:
    """
    Slice 2 stub decider: returns a valid JSON string without external API calls.
    Can use past precedent if available.
    """
    title = incident.get("title", "")
    desc = incident.get("description", "")
    full_text = f"{title} {desc}".lower()

    # Simple heuristic to determine stub category
    if any(k in full_text for k in ["leak", "engine", "valve", "bearing", "pump", "fan"]):
        category = "Hardware"
    elif any(k in full_text for k in ["switch", "network", "cctv", "ais", "gmdss"]):
        category = "Network"
    elif any(k in full_text for k in ["software", "ecdis", "dvr", "alarm"]):
        category = "Software"
    elif any(k in full_text for k in ["fire", "safety", "flooding", "lifeboat", "injury"]):
        category = "Safety"
    else:
        category = "Other"

    # Default severity from precedent or text
    if any(k in full_text for k in ["fire", "flooding", "safety", "high", "drop"]):
        severity = "High"
    elif any(k in full_text for k in ["leak", "alarm", "fault"]):
        severity = "Medium"
    else:
        severity = "Low"

    # Check if a precedent was found
    summary = f"Identified issue affecting {category.lower()} equipment."
    action = "Inspect affected component, isolate if necessary, and schedule repair."
    if past_evidence:
        best = past_evidence[0]
        summary += f" Precedent match #{best['id']} ({best.get('title')})."
        action = f"Apply standard repair procedure following precedent #{best['id']}."

    proposal_dict = {
        "severity": severity,
        "category": category,
        "summary": summary,
        "recommended_action": action,
        "confidence": 0.88,
    }
    return json.dumps(proposal_dict)


def stub_critic(proposal: TriageProposal, past_evidence: list[dict]) -> CriticResult:
    """Slice 2 stub critic: checks proposal against evidence."""
    contradictions = []
    # If confidence is exceptionally low or action empty
    if not proposal.recommended_action:
        contradictions.append("No recommended action provided.")
    verdict = "flagged" if contradictions else "ok"
    return CriticResult(verdict=verdict, contradictions=contradictions)


# ── Agent Loop ───────────────────────────────────────────────────────────────

async def run_triage_loop(
    db: AsyncSession,
    incident: Incident,
    decider_fn: Callable[..., str] | None = None,
    critic_fn: Callable[..., CriticResult] | None = None,
) -> TriageRunResult:
    """
    Execute the framework-free triage agent loop.
    """
    decider = decider_fn or stub_decider
    critic = critic_fn or stub_critic
    steps: list[AgentStep] = []

    # 1. Plan
    keywords = [w for w in incident.title.replace("[SYNTHETIC]", "").split() if len(w) > 3]
    query_plan = " ".join(keywords[:4])
    steps.append(
        AgentStep(
            step="plan",
            details={
                "message": f"Planning evidence gathering for incident #{incident.id}",
                "search_query": query_plan,
            },
        )
    )

    # 2. Act
    past_matches = await search_past_incidents(
        db=db,
        query=query_plan,
        exclude_id=incident.id,
        limit=3,
    )
    detailed_evidence: list[dict] = []
    for match in past_matches[:2]:
        detail = await get_incident(db, match["id"])
        if detail:
            detailed_evidence.append(detail)

    steps.append(
        AgentStep(
            step="act",
            details={
                "matches_found": len(past_matches),
                "inspected_matches": [m["id"] for m in detailed_evidence],
            },
        )
    )

    # 3. Draft & 4. Check (with 1 retry on invalid JSON)
    incident_data = {
        "id": incident.id,
        "title": incident.title,
        "description": incident.description,
    }

    raw_proposal = decider(incident_data, detailed_evidence, retry_error=None)
    steps.append(
        AgentStep(
            step="draft",
            details={"attempt": 1, "raw_response": raw_proposal},
        )
    )

    proposal: TriageProposal | None = None
    try:
        proposal = TriageProposal.model_validate_json(raw_proposal)
        steps.append(
            AgentStep(
                step="check",
                details={"attempt": 1, "status": "valid", "proposal": proposal.model_dump()},
            )
        )
    except Exception as first_err:
        steps.append(
            AgentStep(
                step="check",
                details={"attempt": 1, "status": "invalid", "error": str(first_err)},
            )
        )
        # Retry once
        retry_raw = decider(incident_data, detailed_evidence, retry_error=str(first_err))
        steps.append(
            AgentStep(
                step="draft",
                details={"attempt": 2, "raw_response": retry_raw},
            )
        )
        try:
            proposal = TriageProposal.model_validate_json(retry_raw)
            steps.append(
                AgentStep(
                    step="check",
                    details={"attempt": 2, "status": "valid", "proposal": proposal.model_dump()},
                )
            )
        except Exception as second_err:
            steps.append(
                AgentStep(
                    step="check",
                    details={"attempt": 2, "status": "failed", "error": str(second_err)},
                )
            )
            raise TriageError(f"Failed to generate valid triage proposal after 1 retry: {second_err}") from second_err

    # 5. Rule check (deterministic safety rules)
    adjusted_proposal, rule_result = deterministic_rule_check(
        title=incident.title,
        description=incident.description,
        proposal=proposal,
    )
    steps.append(
        AgentStep(
            step="rule_check",
            details=rule_result.model_dump(),
        )
    )

    # 6. Critic pass
    critic_result = critic(adjusted_proposal, detailed_evidence)
    steps.append(
        AgentStep(
            step="critic",
            details=critic_result.model_dump(),
        )
    )

    # 7. Stop: return complete run result (no auto-approval)
    steps.append(
        AgentStep(
            step="stop",
            details={"status": "completed", "ready_for_human_review": True},
        )
    )

    return TriageRunResult(
        proposal=adjusted_proposal,
        steps=steps,
        rule_check=rule_result,
        critic=critic_result,
    )
