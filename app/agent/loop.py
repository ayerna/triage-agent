"""
Framework-free agent loop for Incident Triage.
Executes the 7 steps:
1. Plan
2. Act (tool calling: sends tool definitions, executes model tool calls or fallback)
3. Draft (JSON structured proposal)
4. Check (Pydantic validation, 1 retry on invalid JSON, fail on second)
5. Rule check (deterministic safety rules)
6. Critic pass (verification against evidence)
7. Stop (return structured triage run result, no auto-approval)
"""
import inspect
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
from app.agent.tools import TOOL_DEFINITIONS, get_incident, search_past_incidents
from app.config import settings
from app.models import Incident


class TriageError(Exception):
    """Raised when triage agent cannot produce valid output after retry."""
    pass


# ── Stub Decider / Critic (used when GROQ_API_KEY is not set or in offline tests) ─

def stub_decider(
    incident: dict,
    past_evidence: list[dict],
    retry_error: str | None = None,
) -> str:
    """
    Offline/stub decider: returns a valid JSON string without external API calls.
    """
    title = incident.get("title", "")
    desc = incident.get("description", "")
    full_text = f"{title} {desc}".lower()

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

    if any(k in full_text for k in ["fire", "flooding", "safety", "high", "drop"]):
        severity = "High"
    elif any(k in full_text for k in ["leak", "alarm", "fault"]):
        severity = "Medium"
    else:
        severity = "Low"

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
    """Offline stub critic: checks proposal against evidence."""
    contradictions = []
    if not proposal.recommended_action:
        contradictions.append("No recommended action provided.")
    verdict = "flagged" if contradictions else "ok"
    return CriticResult(verdict=verdict, contradictions=contradictions)


async def execute_decider(
    decider: Callable,
    incident_data: dict,
    detailed_evidence: list[dict],
    retry_error: str | None = None,
) -> str:
    """Invoke decider function whether it is synchronous or async."""
    if inspect.iscoroutinefunction(decider):
        return await decider(incident_data, detailed_evidence, retry_error=retry_error)
    return decider(incident_data, detailed_evidence, retry_error=retry_error)


async def execute_critic(
    critic: Callable,
    proposal: TriageProposal,
    detailed_evidence: list[dict],
) -> CriticResult:
    """Invoke critic function whether it is synchronous or async."""
    if inspect.iscoroutinefunction(critic):
        return await critic(proposal, detailed_evidence)
    return critic(proposal, detailed_evidence)


# ── Agent Loop ───────────────────────────────────────────────────────────────

async def run_triage_loop(
    db: AsyncSession,
    incident: Incident,
    decider_fn: Callable[..., Any] | None = None,
    critic_fn: Callable[..., Any] | None = None,
    tool_caller_fn: Callable[..., Any] | None = None,
) -> TriageRunResult:
    """
    Execute the framework-free triage agent loop.
    Uses Groq model if GROQ_API_KEY is configured in .env and no override provided.
    Supports real function calling with tool definitions.
    """
    if decider_fn is not None:
        decider = decider_fn
    elif settings.GROQ_API_KEY:
        from app.agent.client import groq_decider
        decider = groq_decider
    else:
        decider = stub_decider

    if critic_fn is not None:
        critic = critic_fn
    elif settings.GROQ_API_KEY:
        from app.agent.client import groq_critic
        critic = groq_critic
    else:
        critic = stub_critic

    if tool_caller_fn is not None:
        tool_caller = tool_caller_fn
    elif settings.GROQ_API_KEY:
        from app.agent.client import groq_tool_caller
        tool_caller = groq_tool_caller
    else:
        tool_caller = None

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

    # 2. Act (Real Function Calling execution)
    incident_data = {
        "id": incident.id,
        "title": incident.title,
        "description": incident.description,
    }

    past_matches: list[dict] = []
    detailed_evidence: list[dict] = []
    tool_calls_executed: list[dict] = []

    if tool_caller is not None:
        try:
            if inspect.iscoroutinefunction(tool_caller):
                requested_calls = await tool_caller(incident_data, TOOL_DEFINITIONS)
            else:
                requested_calls = tool_caller(incident_data, TOOL_DEFINITIONS)
        except Exception:
            requested_calls = []

        for call in requested_calls:
            fn_name = call.get("name")
            args = call.get("arguments", {})
            if fn_name == "search_past_incidents":
                q = args.get("query", query_plan)
                limit = args.get("limit", 3)
                matches = await search_past_incidents(db, query=q, exclude_id=incident.id, limit=limit)
                past_matches.extend(matches)
                tool_calls_executed.append({
                    "tool": "search_past_incidents",
                    "arguments": args,
                    "matches_count": len(matches),
                })
                for m in matches[:2]:
                    det = await get_incident(db, m["id"])
                    if det and not any(e["id"] == det["id"] for e in detailed_evidence):
                        detailed_evidence.append(det)

            elif fn_name == "get_incident":
                inc_id = args.get("incident_id")
                if inc_id is not None:
                    det = await get_incident(db, int(inc_id))
                    if det and not any(e["id"] == det["id"] for e in detailed_evidence):
                        detailed_evidence.append(det)
                    tool_calls_executed.append({
                        "tool": "get_incident",
                        "arguments": args,
                        "found": bool(det),
                    })

    # If tool caller produced no matches/calls (or running offline stub without tool_caller):
    if not past_matches and not detailed_evidence:
        past_matches = await search_past_incidents(
            db=db,
            query=query_plan,
            exclude_id=incident.id,
            limit=3,
        )
        for match in past_matches[:2]:
            detail = await get_incident(db, match["id"])
            if detail and not any(e["id"] == detail["id"] for e in detailed_evidence):
                detailed_evidence.append(detail)
        tool_calls_executed.append({
            "tool": "search_past_incidents (fallback)",
            "query": query_plan,
            "matches_count": len(past_matches),
        })

    steps.append(
        AgentStep(
            step="act",
            details={
                "tool_calls": tool_calls_executed,
                "matches_found": len(past_matches),
                "inspected_matches": [m["id"] for m in detailed_evidence],
            },
        )
    )

    # 3. Draft & 4. Check (with 1 retry on invalid JSON)
    raw_proposal = await execute_decider(decider, incident_data, detailed_evidence, retry_error=None)
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
        retry_raw = await execute_decider(decider, incident_data, detailed_evidence, retry_error=str(first_err))
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
    critic_result = await execute_critic(critic, adjusted_proposal, detailed_evidence)
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
