"""
LangGraph state machine re-implementation of the Incident Triage agent loop.
Nodes:
- plan: determine search plan
- tools: execute function calls / tools against PostgreSQL
- draft: call decider (Groq or stub)
- check: validate JSON with Pydantic; routes to retry edge on invalid output
- rule_check: deterministic safety rules
- critic: critique proposal against evidence
- handle_failure: fail gracefully after 1 retry

Both implementations (framework-free in loop.py and LangGraph here) live
side-by-side in the repository so they can be directly compared.
"""
import inspect
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
from app.agent.tools import TOOL_DEFINITIONS, get_incident, search_past_incidents
from app.config import settings
from app.models import Incident


class TriageGraphState(TypedDict, total=False):
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
    tool_caller_fn: Callable[..., Any] | None = None,
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

    if tool_caller_fn is not None:
        tool_caller = tool_caller_fn
    elif settings.GROQ_API_KEY:
        from app.agent.client import groq_tool_caller
        tool_caller = groq_tool_caller
    else:
        tool_caller = None

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
        query_plan = state.get("plan_query", "")
        past_matches: list[dict] = []
        detailed_evidence: list[dict] = []
        tool_calls_executed: list[dict] = []

        if tool_caller is not None:
            try:
                if inspect.iscoroutinefunction(tool_caller):
                    requested_calls = await tool_caller(inc, TOOL_DEFINITIONS)
                else:
                    requested_calls = tool_caller(inc, TOOL_DEFINITIONS)
            except Exception:
                requested_calls = []

            for call in requested_calls:
                fn_name = call.get("name")
                args = call.get("arguments", {})
                if fn_name == "search_past_incidents":
                    q = args.get("query", query_plan)
                    limit = args.get("limit", 3)
                    matches = await search_past_incidents(db, query=q, exclude_id=inc.get("id"), limit=limit)
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

        if not past_matches and not detailed_evidence:
            past_matches = await search_past_incidents(
                db=db,
                query=query_plan,
                exclude_id=inc.get("id"),
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

        step = AgentStep(
            step="act",
            details={
                "tool_calls": tool_calls_executed,
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
                "steps": [*state.get("steps", []), step.model_dump()],
            }
        except Exception as err:
            if attempt == 1:
                step = AgentStep(
                    step="check",
                    details={"attempt": 1, "status": "invalid", "error": str(err)},
                )
                return {
                    "validation_error": str(err),
                    "is_valid": False,
                    "is_failed": False,
                    "steps": [*state.get("steps", []), step.model_dump()],
                }
            else:
                step = AgentStep(
                    step="check",
                    details={"attempt": 2, "status": "failed", "error": str(err)},
                )
                return {
                    "validation_error": str(err),
                    "is_valid": False,
                    "is_failed": True,
                    "steps": [*state.get("steps", []), step.model_dump()],
                }

    # ── Node 5: Rule Check ───────────────────────────────────────────────────
    async def rule_check_node(state: TriageGraphState) -> dict:
        inc = state["incident"]
        raw_p = state["proposal"]
        p = TriageProposal(**raw_p) if raw_p else None

        adjusted, rule_result = deterministic_rule_check(
            title=inc.get("title", ""),
            description=inc.get("description", ""),
            proposal=p,
        )
        step = AgentStep(
            step="rule_check",
            details=rule_result.model_dump(),
        )
        return {
            "proposal": adjusted.model_dump() if adjusted else None,
            "rule_check": rule_result.model_dump(),
            "steps": [*state.get("steps", []), step.model_dump()],
        }

    # ── Node 6: Critic ───────────────────────────────────────────────────────
    async def critic_node(state: TriageGraphState) -> dict:
        raw_p = state["proposal"]
        p = TriageProposal(**raw_p) if raw_p else None
        evidence = state.get("detailed_evidence", [])

        if p is not None:
            critic_res = await execute_critic(critic, p, evidence)
        else:
            critic_res = CriticResult(verdict="ok", contradictions=[])

        critic_step = AgentStep(
            step="critic",
            details=critic_res.model_dump(),
        )
        stop_step = AgentStep(
            step="stop",
            details={"status": "completed", "ready_for_human_review": True},
        )
        return {
            "critic": critic_res.model_dump(),
            "steps": [
                *state.get("steps", []),
                critic_step.model_dump(),
                stop_step.model_dump(),
            ],
        }

    # ── Node 7: Handle Failure ───────────────────────────────────────────────
    async def handle_failure_node(state: TriageGraphState) -> dict:
        err = state.get("validation_error", "Unknown validation error")
        raise TriageError(f"Failed to generate valid triage proposal after 1 retry: {err}")

    # ── Conditional Routing Function ─────────────────────────────────────────
    def route_check(state: TriageGraphState) -> str:
        if state.get("is_valid"):
            return "rule_check"
        elif state.get("is_failed"):
            return "handle_failure"
        else:
            return "draft"

    # ── Build StateGraph ─────────────────────────────────────────────────────
    workflow = StateGraph(TriageGraphState)

    workflow.add_node("plan", plan_node)
    workflow.add_node("tools", tools_node)
    workflow.add_node("draft", draft_node)
    workflow.add_node("check", check_node)
    workflow.add_node("rule_check", rule_check_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("handle_failure", handle_failure_node)

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
    tool_caller_fn: Callable[..., Any] | None = None,
) -> TriageRunResult:
    """Execute triage using the LangGraph state machine graph."""
    app_graph = create_triage_graph(
        db=db,
        decider_fn=decider_fn,
        critic_fn=critic_fn,
        tool_caller_fn=tool_caller_fn,
    )

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

    proposal_data = final_state.get("proposal")
    if not proposal_data:
        raise TriageError("LangGraph loop ended without valid proposal")

    return TriageRunResult(
        proposal=TriageProposal(**proposal_data),
        steps=[AgentStep(**s) for s in final_state.get("steps", [])],
        rule_check=RuleCheckResult(**(final_state.get("rule_check") or {"status": "clean"})),
        critic=CriticResult(**(final_state.get("critic") or {"verdict": "ok", "contradictions": []})),
    )
