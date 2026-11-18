"""
LangGraph implementation of the Incident Triage Agent loop.
Nodes:
- plan: decides what evidence is needed
- tools: calls search_past_incidents and get_incident
- draft: generates structured JSON proposal
- check: validates with Pydantic; routes back to draft on first error, fails on second
- rule_check: applies deterministic safety rules
- critic: critic pass re-reading proposal against evidence
"""
from typing import Any, Callable, TypedDict
from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop import TriageError, execute_critic, execute_decider, stub_critic, stub_decider
from app.agent.rules import deterministic_rule_check
from app.agent.schemas import (
    AgentStep,
    CriticResult,
    RuleCheckResult,
    TriageProposal,
    TriageRunResult,
)
from app.agent.tools import get_incident, search_past_incidents
from app.config import settings
from app.models import Incident


class TriageGraphState(TypedDict):
    incident: dict
    plan_query: str
    detailed_evidence: list[dict]
    raw_proposal: str
    proposal: dict | None
    attempts: int
    validation_error: str | None
    is_valid: bool
    is_failed: bool
    rule_check: dict | None
    critic: dict | None
    steps: list[dict]


def create_triage_graph(
    db: AsyncSession,
    decider_fn: Callable[..., Any] | None = None,
    critic_fn: Callable[..., Any] | None = None,
):
    """Build and compile the LangGraph StateGraph for triage."""

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

    # ── Node 1: Plan ─────────────────────────────────────────────────────────
    async def plan_node(state: TriageGraphState) -> dict:
        inc = state["incident"]
        keywords = [w for w in inc.get("title", "").replace("[SYNTHETIC]", "").split() if len(w) > 3]
        query_plan = " ".join(keywords[:4])
        step = AgentStep(
            step="plan",
            details={
                "message": f"Planning evidence gathering for incident #{inc.get('id')}",
                "search_query": query_plan,
            },
        )
        return {
            "plan_query": query_plan,
            "steps": [*state.get("steps", []), step.model_dump()],
        }

    # ── Node 2: Tools ────────────────────────────────────────────────────────
    async def tools_node(state: TriageGraphState) -> dict:
        inc = state["incident"]
        past_matches = await search_past_incidents(
            db=db,
            query=state["plan_query"],
            exclude_id=inc.get("id"),
            limit=3,
        )
        detailed_evidence: list[dict] = []
        for match in past_matches[:2]:
            detail = await get_incident(db, match["id"])
            if detail:
                detailed_evidence.append(detail)

        step = AgentStep(
            step="act",
            details={
                "matches_found": len(past_matches),
                "inspected_matches": [m["id"] for m in detailed_evidence],
            },
        )
        return {
            "detailed_evidence": detailed_evidence,
            "steps": [*state.get("steps", []), step.model_dump()],
        }

    # ── Node 3: Draft ────────────────────────────────────────────────────────
    async def draft_node(state: TriageGraphState) -> dict:
        attempt = state.get("attempts", 0) + 1
        raw_resp = await execute_decider(
            decider,
            state["incident"],
            state.get("detailed_evidence", []),
            retry_error=state.get("validation_error"),
        )
        step = AgentStep(
            step="draft",
            details={"attempt": attempt, "raw_response": raw_resp},
        )
        return {
            "raw_proposal": raw_resp,
            "attempts": attempt,
            "steps": [*state.get("steps", []), step.model_dump()],
        }

    # ── Node 4: Check ────────────────────────────────────────────────────────
    async def check_node(state: TriageGraphState) -> dict:
        attempt = state["attempts"]
        raw = state["raw_proposal"]
        try:
            parsed = TriageProposal.model_validate_json(raw)
            step = AgentStep(
                step="check",
                details={"attempt": attempt, "status": "valid", "proposal": parsed.model_dump()},
            )
            return {
                "proposal": parsed.model_dump(),
                "is_valid": True,
                "is_failed": False,
                "validation_error": None,
                "steps": [*state.get("steps", []), step.model_dump()],
            }
        except Exception as e:
            if attempt >= 2:
                step = AgentStep(
                    step="check",
                    details={"attempt": attempt, "status": "failed", "error": str(e)},
                )
                return {
                    "is_valid": False,
                    "is_failed": True,
                    "validation_error": str(e),
                    "steps": [*state.get("steps", []), step.model_dump()],
                }
            else:
                step = AgentStep(
                    step="check",
                    details={"attempt": attempt, "status": "invalid", "error": str(e)},
                )
                return {
                    "is_valid": False,
                    "is_failed": False,
                    "validation_error": str(e),
                    "steps": [*state.get("steps", []), step.model_dump()],
                }

    # ── Router function for Check conditional edge ───────────────────────────
    def route_check(state: TriageGraphState) -> str:
        if state["is_valid"]:
            return "rule_check"
        elif state["is_failed"]:
            return "handle_failure"
        return "draft"  # Retry edge

    # ── Failure handler node ─────────────────────────────────────────────────
    async def failure_node(state: TriageGraphState) -> dict:
        raise TriageError(
            f"Failed to generate valid triage proposal after 1 retry: {state.get('validation_error')}"
        )

    # ── Node 5: Rule Check ───────────────────────────────────────────────────
    async def rule_check_node(state: TriageGraphState) -> dict:
        inc = state["incident"]
        raw_prop = state["proposal"]
        proposal_obj = TriageProposal.model_validate(raw_prop)

        adjusted_prop, rule_res = deterministic_rule_check(
            title=inc.get("title", ""),
            description=inc.get("description", ""),
            proposal=proposal_obj,
        )
        step = AgentStep(step="rule_check", details=rule_res.model_dump())
        return {
            "proposal": adjusted_prop.model_dump(),
            "rule_check": rule_res.model_dump(),
            "steps": [*state.get("steps", []), step.model_dump()],
        }

    # ── Node 6: Critic ───────────────────────────────────────────────────────
    async def critic_node(state: TriageGraphState) -> dict:
        proposal_obj = TriageProposal.model_validate(state["proposal"])
        critic_res = await execute_critic(
            critic,
            proposal_obj,
            state.get("detailed_evidence", []),
        )
        step = AgentStep(step="critic", details=critic_res.model_dump())
        stop_step = AgentStep(step="stop", details={"status": "completed", "ready_for_human_review": True})
        return {
            "critic": critic_res.model_dump(),
            "steps": [*state.get("steps", []), step.model_dump(), stop_step.model_dump()],
        }

    # ── Construct the StateGraph ─────────────────────────────────────────────
    workflow = StateGraph(TriageGraphState)

    workflow.add_node("plan", plan_node)
    workflow.add_node("tools", tools_node)
    workflow.add_node("draft", draft_node)
    workflow.add_node("check", check_node)
    workflow.add_node("handle_failure", failure_node)
    workflow.add_node("rule_check", rule_check_node)
    workflow.add_node("critic", critic_node)

    workflow.set_entry_point("plan")
    workflow.add_edge("plan", "tools")
    workflow.add_edge("tools", "draft")
    workflow.add_edge("draft", "check")

    workflow.add_conditional_edges(
        "check",
        route_check,
        {
            "rule_check": "rule_check",
            "draft": "draft",
            "handle_failure": "handle_failure",
        },
    )

    workflow.add_edge("rule_check", "critic")
    workflow.add_edge("critic", END)

    return workflow.compile()


async def run_langgraph_triage_loop(
    db: AsyncSession,
    incident: Incident,
    decider_fn: Callable[..., Any] | None = None,
    critic_fn: Callable[..., Any] | None = None,
) -> TriageRunResult:
    """Execute triage using the LangGraph state machine graph."""
    app_graph = create_triage_graph(db=db, decider_fn=decider_fn, critic_fn=critic_fn)

    initial_state: TriageGraphState = {
        "incident": {
            "id": incident.id,
            "title": incident.title,
            "description": incident.description,
        },
        "plan_query": "",
        "detailed_evidence": [],
        "raw_proposal": "",
        "proposal": None,
        "attempts": 0,
        "validation_error": None,
        "is_valid": False,
        "is_failed": False,
        "rule_check": None,
        "critic": None,
        "steps": [],
    }

    final_state = await app_graph.ainvoke(initial_state)

    proposal = TriageProposal.model_validate(final_state["proposal"])
    rule_check = RuleCheckResult.model_validate(final_state["rule_check"])
    critic = CriticResult.model_validate(final_state["critic"])
    steps = [AgentStep.model_validate(s) for s in final_state["steps"]]

    return TriageRunResult(
        proposal=proposal,
        steps=steps,
        rule_check=rule_check,
        critic=critic,
    )
