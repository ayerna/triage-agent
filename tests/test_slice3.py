"""
Slice 3 tests:
- Groq client and cleaner functions
- Real function calling: tool definitions in Groq payload, parsing, execution in loop
- LangGraph implementation of the triage loop
- LangGraph retry edge handling (attempt 1 invalid -> attempt 2 valid)
- LangGraph retry failure edge (attempt 1 & 2 invalid -> TriageError)
- API endpoint support for both engine='framework_free' and engine='langgraph'
- Optional live Groq API call (skipped if GROQ_API_KEY is unset)
- Real JOIN verification on GET /incidents
"""
import json
import pytest
from unittest.mock import AsyncMock, patch

from app.agent.client import (
    clean_json_text,
    groq_critic,
    groq_decider,
    groq_tool_caller,
)
from app.agent.graph import run_langgraph_triage_loop
from app.agent.loop import TriageError, run_triage_loop
from app.agent.schemas import CriticResult, TriageProposal
from app.agent.tools import TOOL_DEFINITIONS
from app.config import settings
from app.models import Incident, SeverityEnum, StatusEnum, TriageRun


# ── 1. Client Unit Tests ─────────────────────────────────────────────────────

def test_clean_json_text_strips_markdown():
    # Markdown json fencing
    raw1 = "```json\n{\"severity\": \"High\", \"category\": \"Hardware\"}\n```"
    assert clean_json_text(raw1) == '{"severity": "High", "category": "Hardware"}'

    # Generic code fencing
    raw2 = "```\n{\"severity\": \"Low\"}\n```"
    assert clean_json_text(raw2) == '{"severity": "Low"}'

    # Already clean JSON
    raw3 = '{"severity": "Medium"}'
    assert clean_json_text(raw3) == '{"severity": "Medium"}'


@pytest.mark.asyncio
async def test_groq_decider_prompt_and_parsing():
    fake_response = json.dumps({
        "severity": "High",
        "category": "Hardware",
        "summary": "Fuel pump actuator seizure detected",
        "recommended_action": "Replace actuator and flush line",
        "confidence": 0.92,
    })

    with patch("app.agent.client.call_groq_chat", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = fake_response

        incident = {"id": 1, "title": "Fuel Pump Fault", "description": "Pressure drop"}
        evidence = [{"id": 2, "title": "Past Fuel Issue", "severity": "High", "status": "Approved"}]

        res = await groq_decider(incident, evidence)
        assert mock_call.called
        # Verify prompt instructions
        called_messages = mock_call.call_args[0][0]
        assert any("Allowed categories: [Hardware" in m["content"] for m in called_messages)

        # Verify parsed result matches Pydantic schema
        proposal = TriageProposal.model_validate_json(res)
        assert proposal.severity == "High"
        assert proposal.category == "Hardware"


@pytest.mark.asyncio
async def test_groq_critic_parsing():
    fake_response = json.dumps({
        "verdict": "ok",
        "contradictions": [],
    })

    with patch("app.agent.client.call_groq_chat", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = fake_response

        proposal = TriageProposal(
            severity="Medium",
            category="Hardware",
            summary="Lube oil pressure fluctuating",
            recommended_action="Inspect sensor wiring",
            confidence=0.85,
        )
        critic_res = await groq_critic(proposal, [])
        assert critic_res.verdict == "ok"
        assert critic_res.contradictions == []


# ── 2. Real Function Calling Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_groq_tool_caller_payload_and_parsing():
    """
    Verify that groq_tool_caller sends the two tool definitions in the Groq payload,
    and cleanly parses returned tool calls from the model.
    """
    mock_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_search_01",
                "type": "function",
                "function": {
                    "name": "search_past_incidents",
                    "arguments": json.dumps({"query": "cooling pump leak", "limit": 2}),
                },
            }
        ],
    }

    with patch("app.agent.client.call_groq_completion", new_callable=AsyncMock) as mock_completion:
        mock_completion.return_value = mock_message

        incident = {
            "id": 10,
            "title": "Main SW Cooling Pump Leak",
            "description": "Shaft gland dripping 20 drops per minute",
        }

        parsed_calls = await groq_tool_caller(incident, TOOL_DEFINITIONS)

        assert mock_completion.called
        _, kwargs = mock_completion.call_args
        # Verify tool definitions were sent in the payload
        assert kwargs["tools"] == TOOL_DEFINITIONS
        assert len(kwargs["tools"]) == 2
        tool_names = [t["function"]["name"] for t in kwargs["tools"]]
        assert "search_past_incidents" in tool_names
        assert "get_incident" in tool_names

        # Verify returned parsed tool calls
        assert len(parsed_calls) == 1
        assert parsed_calls[0]["name"] == "search_past_incidents"
        assert parsed_calls[0]["arguments"] == {"query": "cooling pump leak", "limit": 2}


@pytest.mark.asyncio
async def test_loop_executes_mocked_tool_call(db_session, test_user):
    """
    Verify that the loop executes the tool call returned by the model,
    queries PostgreSQL, records the tool call details in the 'act' step,
    and feeds the evidence into the proposal drafting.
    """
    # 1. Seed past precedent incident
    past_inc = Incident(
        title="Main SW Cooling Pump Gland Leak",
        description="Past incident with pump gland packing replacement",
        category="Hardware",
        severity=SeverityEnum.High,
        status=StatusEnum.Approved,
        created_by=test_user.id,
    )
    # 2. Target incident being triaged
    target_inc = Incident(
        title="Emergency SW Cooling Pump Failure",
        description="Cooling pump shaft gland leaking seawater",
        created_by=test_user.id,
    )
    db_session.add_all([past_inc, target_inc])
    await db_session.commit()

    # Mock tool caller returning a tool call for search_past_incidents
    async def mock_tool_caller(incident_dict, tools):
        return [
            {
                "id": "call_123",
                "name": "search_past_incidents",
                "arguments": {"query": "Cooling Pump", "limit": 2},
            }
        ]

    # Run the loop with our mock tool caller
    result = await run_triage_loop(db_session, target_inc, tool_caller_fn=mock_tool_caller)

    # Verify 'act' step recorded real tool call execution
    act_step = [s for s in result.steps if s.step == "act"][0]
    executed = act_step.details.get("tool_calls", [])
    assert len(executed) >= 1
    assert executed[0]["tool"] == "search_past_incidents"
    assert executed[0]["arguments"] == {"query": "Cooling Pump", "limit": 2}
    assert executed[0]["matches_count"] >= 1

    # Verify inspected matches contain our seeded precedent
    assert past_inc.id in act_step.details["inspected_matches"]
    assert result.proposal.category == "Hardware"


# ── 3. LangGraph Loop Tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_langgraph_loop_runs_and_produces_valid_result(db_session, test_user):
    inc = Incident(
        title="Main Sea Water Pump Packing Leak",
        description="Leak from shaft seal of SW cooling pump SWP-01, 15 drips per min",
        created_by=test_user.id,
    )
    db_session.add(inc)
    await db_session.commit()

    res = await run_langgraph_triage_loop(db_session, inc)
    assert res.proposal.category in ["Hardware", "Software", "Network", "Safety", "Other"]
    assert res.proposal.severity in ["Low", "Medium", "High", "Critical"]
    assert res.rule_check is not None
    assert res.critic is not None

    # Verify all expected graph steps were recorded
    step_names = [s.step for s in res.steps]
    assert "plan" in step_names
    assert "act" in step_names
    assert "draft" in step_names
    assert "check" in step_names
    assert "rule_check" in step_names
    assert "critic" in step_names
    assert "stop" in step_names


@pytest.mark.asyncio
async def test_langgraph_loop_retries_on_bad_json_then_succeeds(db_session, test_user):
    inc = Incident(
        title="Boiler Burner Solenoid Valve Failure",
        description="Solenoid valve SV-102 failing to energize on startup",
        created_by=test_user.id,
    )
    db_session.add(inc)
    await db_session.commit()

    calls = 0

    def retry_decider(incident_dict, evidence, retry_error=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "GARBAGE_JSON_OUTPUT"
        return json.dumps({
            "severity": "Medium",
            "category": "Hardware",
            "summary": "Burner solenoid coil open circuit",
            "recommended_action": "Replace 24V solenoid coil",
            "confidence": 0.91,
        })

    res = await run_langgraph_triage_loop(db_session, inc, decider_fn=retry_decider)
    assert calls == 2
    assert res.proposal.severity == "Medium"
    draft_steps = [s for s in res.steps if s.step == "draft"]
    assert len(draft_steps) == 2


@pytest.mark.asyncio
async def test_langgraph_loop_fails_after_retry_limit(db_session, test_user):
    inc = Incident(
        title="ECDIS Display Artifacts",
        description="Radar overlay distorted on master ECDIS screen",
        created_by=test_user.id,
    )
    db_session.add(inc)
    await db_session.commit()

    def always_broken_decider(incident_dict, evidence, retry_error=None):
        return "STILL_INVALID_JSON"

    with pytest.raises(TriageError) as exc_info:
        await run_langgraph_triage_loop(db_session, inc, decider_fn=always_broken_decider)

    assert "Failed to generate valid triage proposal after 1 retry" in str(exc_info.value)


@pytest.mark.asyncio
async def test_langgraph_deterministic_rules_escalation(db_session, test_user):
    # Safety keyword in incident text
    inc = Incident(
        title="Fire damper flap jammed on Deck B",
        description="Ventilation fire damper flap failed to close on test signal",
        created_by=test_user.id,
    )
    db_session.add(inc)
    await db_session.commit()

    # Stub decider proposing 'Low' severity
    def low_decider(incident_dict, evidence, retry_error=None):
        return json.dumps({
            "severity": "Low",
            "category": "Safety",
            "summary": "Fire damper linkage needs grease",
            "recommended_action": "Lubricate actuator mechanism",
            "confidence": 0.82,
        })

    res = await run_langgraph_triage_loop(db_session, inc, decider_fn=low_decider)
    # The deterministic rule MUST force severity to High
    assert res.proposal.severity == "High"
    assert res.rule_check.forced_by_rule is True
    assert "fire" in res.rule_check.rule_reason.lower()


# ── 4. API Route with Engine Selection ───────────────────────────────────────

@pytest.mark.asyncio
async def test_api_triage_with_langgraph_engine(client, auth_headers):
    # 1. Create incident
    create_resp = await client.post(
        "/incidents",
        json={"title": "Auxiliary Condenser Tube Leak", "description": "Conductivity alarm high in condensate return"},
        headers=auth_headers,
    )
    inc_id = create_resp.json()["id"]

    # 2. Run triage with engine=langgraph
    triage_resp = await client.post(
        f"/incidents/{inc_id}/triage?engine=langgraph",
        headers=auth_headers,
    )
    assert triage_resp.status_code == 201
    data = triage_resp.json()
    assert data["incident_id"] == inc_id

    # Verify incident updated
    inc_resp = await client.get(f"/incidents/{inc_id}", headers=auth_headers)
    assert inc_resp.json()["status"] == "Triaged"


# ── 5. Real JOIN Query Verification ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_incidents_real_join_with_latest_triage_id(client, auth_headers):
    """
    Verify GET /incidents uses the single JOIN query to fetch both incidents
    and correctly attach the latest triage_run id.
    """
    # 1. Create incident A (triaged)
    resp_a = await client.post(
        "/incidents",
        json={"title": "Main Engine Turbocharger Surge", "description": "Surging at 85% MCR"},
        headers=auth_headers,
    )
    id_a = resp_a.json()["id"]

    # 2. Create incident B (untriaged)
    resp_b = await client.post(
        "/incidents",
        json={"title": "Galley Refrigerator Compressor Trip", "description": "Thermal overload trip"},
        headers=auth_headers,
    )
    id_b = resp_b.json()["id"]

    # 3. Triage incident A
    triage_resp = await client.post(f"/incidents/{id_a}/triage", headers=auth_headers)
    assert triage_resp.status_code == 201
    triage_id_a = triage_resp.json()["id"]

    # 4. Fetch list via GET /incidents (executed with single outerjoin query)
    list_resp = await client.get("/incidents", headers=auth_headers)
    assert list_resp.status_code == 200
    items = list_resp.json()

    item_a = next(i for i in items if i["id"] == id_a)
    item_b = next(i for i in items if i["id"] == id_b)

    # Incident A must have latest_triage_id populated
    assert item_a["latest_triage_id"] == triage_id_a
    assert item_a["status"] == "Triaged"

    # Incident B must have latest_triage_id as None
    assert item_b["latest_triage_id"] is None
    assert item_b["status"] == "Open"


# ── 6. Optional Live Groq API Test ───────────────────────────────────────────

@pytest.mark.skipif(not settings.GROQ_API_KEY, reason="GROQ_API_KEY not configured in .env")
@pytest.mark.asyncio
async def test_live_groq_api_call(db_session, test_user):
    """
    Executes an actual call to the hosted Groq API (openai/gpt-oss-20b)
    when GROQ_API_KEY is present in .env.
    """
    inc = Incident(
        title="[SYNTHETIC] Emergency Fire Pump Suction Valve Jammed",
        description="Emergency fire pump FP-02 suction valve handwheel seized in half-closed position during safety drill.",
        created_by=test_user.id,
    )
    db_session.add(inc)
    await db_session.commit()

    # Call framework-free loop with live model
    res = await run_triage_loop(db_session, inc)
    assert res.proposal.severity in ["High", "Critical"]  # 'fire' forces High+
    assert res.proposal.category in ["Hardware", "Safety"]
    assert len(res.proposal.summary) > 5
    assert len(res.steps) >= 6
