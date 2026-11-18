"""
Slice 2 tests:
- Framework-free loop with stub decider
- Both tools reading Postgres (search_past_incidents, get_incident)
- JSON schema validation & 1 retry on invalid JSON
- Deterministic safety rules (fire, safety, flooding, injury -> High)
- Triage run + decisions persistence
- Transaction + rollback test (verifying atomic status update and decision log)
"""
import json
import pytest
from unittest.mock import patch
from sqlalchemy import select

from app.agent.loop import TriageError, run_triage_loop, stub_decider
from app.agent.rules import deterministic_rule_check
from app.agent.schemas import TriageProposal
from app.agent.tools import get_incident, search_past_incidents
from app.models import Decision, Incident, StatusEnum, TriageRun


# ── 1. Tools Unit Tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tools_search_and_get(db_session, test_user):
    # Create two past incidents
    inc1 = Incident(
        title="Lube oil pressure pump fail",
        description="Auxiliary lube oil pump stopped running",
        category="Hardware",
        status=StatusEnum.Approved,
        created_by=test_user.id,
    )
    inc2 = Incident(
        title="Bridge radar switch failure",
        description="Network switch lost connection to ECDIS",
        category="Network",
        status=StatusEnum.Approved,
        created_by=test_user.id,
    )
    db_session.add_all([inc1, inc2])
    await db_session.commit()

    # Tool 1: search_past_incidents
    results = await search_past_incidents(db_session, query="lube oil pump", exclude_id=None)
    assert len(results) >= 1
    matched_ids = [r["id"] for r in results]
    assert inc1.id in matched_ids
    first = results[0]
    assert "id" in first and "title" in first and "severity" in first and "status" in first

    # Tool 2: get_incident
    detail = await get_incident(db_session, inc1.id)
    assert detail is not None
    assert detail["title"] == "Lube oil pressure pump fail"
    assert detail["category"] == "Hardware"


# ── 2. Deterministic Rules Tests ─────────────────────────────────────────────

def test_deterministic_rules_escalates_safety():
    # Normal proposal is Low
    proposal = TriageProposal(
        severity="Low",
        category="Hardware",
        summary="Minor pressure drop noted",
        recommended_action="Inspect valve",
        confidence=0.9,
    )

    # Keywords: fire, safety, flooding, injury
    keywords = ["fire", "safety", "flooding", "injury"]
    for kw in keywords:
        adj_proposal, rule_res = deterministic_rule_check(
            title=f"Warning: {kw} detected in forward hold",
            description="Inspection required",
            proposal=proposal,
        )
        assert adj_proposal.severity == "High", f"Keyword {kw} failed to escalate severity to High"
        assert rule_res.forced_by_rule is True
        assert kw in rule_res.rule_reason.lower()


def test_deterministic_rules_keeps_critical():
    # If already Critical, rule check should not downgrade
    proposal = TriageProposal(
        severity="Critical",
        category="Safety",
        summary="Severe flooding risk in cargo hold",
        recommended_action="Activate bilge pump immediately",
        confidence=0.95,
    )
    adj_proposal, rule_res = deterministic_rule_check(
        title="Flooding alarm active",
        description="Water ingress in hold 2",
        proposal=proposal,
    )
    assert adj_proposal.severity == "Critical"
    assert rule_res.forced_by_rule is False


# ── 3. JSON Validation & Retry Tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_loop_retries_once_on_bad_json_then_succeeds(db_session, test_user):
    inc = Incident(
        title="Generator RPM instability",
        description="Emergency generator hunting between 1400 and 1600 RPM",
        created_by=test_user.id,
    )
    db_session.add(inc)
    await db_session.commit()

    call_count = 0

    def faulty_decider(incident_dict, evidence, retry_error=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "NOT VALID JSON"
        # Second call returns valid JSON
        return json.dumps({
            "severity": "Medium",
            "category": "Hardware",
            "summary": "Generator governor malfunction",
            "recommended_action": "Tune governor actuator",
            "confidence": 0.85,
        })

    res = await run_triage_loop(db_session, inc, decider_fn=faulty_decider)
    assert call_count == 2
    assert res.proposal.severity == "Medium"
    # Verify steps show first attempt failure and retry success
    draft_steps = [s for s in res.steps if s.step == "draft"]
    assert len(draft_steps) == 2


@pytest.mark.asyncio
async def test_loop_fails_if_json_invalid_after_retry(db_session, test_user):
    inc = Incident(
        title="Compressor valve leak",
        description="Compressed air receiver leaking at drain valve",
        created_by=test_user.id,
    )
    db_session.add(inc)
    await db_session.commit()

    def always_broken_decider(incident_dict, evidence, retry_error=None):
        return "{invalid_json_always}"

    with pytest.raises(TriageError) as exc_info:
        await run_triage_loop(db_session, inc, decider_fn=always_broken_decider)

    assert "Failed to generate valid triage proposal after 1 retry" in str(exc_info.value)


# ── 4. API Triage Endpoint ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_triage_endpoint_runs_agent_and_persists(client, auth_headers):
    # 1. Create incident
    create_resp = await client.post(
        "/incidents",
        json={"title": "Aux Boiler Flame Failure", "description": "Aux boiler tripped on flame failure during startup"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    inc_id = create_resp.json()["id"]

    # 2. Run triage
    triage_resp = await client.post(f"/incidents/{inc_id}/triage", headers=auth_headers)
    assert triage_resp.status_code == 201
    triage_data = triage_resp.json()
    assert triage_data["incident_id"] == inc_id
    assert "proposal_json" in triage_data
    assert "steps_json" in triage_data

    # Parse and inspect steps
    steps = json.loads(triage_data["steps_json"])
    step_names = [s["step"] for s in steps]
    for required_step in ["plan", "act", "draft", "check", "rule_check", "critic", "stop"]:
        assert required_step in step_names

    # 3. Verify incident status changed to Triaged
    get_resp = await client.get(f"/incidents/{inc_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "Triaged"
    assert get_resp.json()["severity"] is not None
    assert get_resp.json()["category"] is not None


# ── 5. Decision Endpoint & Transaction Rollback Test ─────────────────────────

@pytest.mark.asyncio
async def test_decision_endpoint_approved(client, auth_headers):
    # Create and triage an incident
    create_resp = await client.post(
        "/incidents",
        json={"title": "Ballast Pump Bearing Vibration", "description": "High vibration on ballast pump P-01"},
        headers=auth_headers,
    )
    inc_id = create_resp.json()["id"]
    await client.post(f"/incidents/{inc_id}/triage", headers=auth_headers)

    # Approve decision
    dec_resp = await client.post(
        f"/incidents/{inc_id}/decision",
        json={"decision": "Approved", "note": "Agreed with hardware classification"},
        headers=auth_headers,
    )
    assert dec_resp.status_code == 201
    dec_data = dec_resp.json()
    assert dec_data["decision"] == "Approved"
    assert dec_data["note"] == "Agreed with hardware classification"

    # Verify incident status is Approved
    inc_resp = await client.get(f"/incidents/{inc_id}", headers=auth_headers)
    assert inc_resp.json()["status"] == "Approved"


@pytest.mark.asyncio
async def test_decision_endpoint_cannot_decide_untriaged(client, auth_headers):
    create_resp = await client.post(
        "/incidents",
        json={"title": "Untriaged issue", "description": "Not triaged yet"},
        headers=auth_headers,
    )
    inc_id = create_resp.json()["id"]

    # Decision on untriaged must return 400
    dec_resp = await client.post(
        f"/incidents/{inc_id}/decision",
        json={"decision": "Approved"},
        headers=auth_headers,
    )
    assert dec_resp.status_code == 400
    assert "not been triaged" in dec_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_decision_transaction_rollback_on_error(client, auth_headers, db_session):
    """
    CRITICAL REQUIREMENT:
    Write the decision row AND update incident status in ONE database transaction,
    rolling back both on error.
    We simulate a failure during the commit of the decision endpoint and verify:
    1. Incident status remains 'Triaged' (NOT updated to Approved).
    2. No decision record exists in the database for this triage run.
    """
    # 1. Create and triage incident
    create_resp = await client.post(
        "/incidents",
        json={"title": "Steering Ram Leak Test", "description": "Hydraulic oil leak on steering ram"},
        headers=auth_headers,
    )
    inc_id = create_resp.json()["id"]
    triage_resp = await client.post(f"/incidents/{inc_id}/triage", headers=auth_headers)
    triage_run_id = triage_resp.json()["id"]

    # Verify status is currently Triaged
    before_resp = await client.get(f"/incidents/{inc_id}", headers=auth_headers)
    assert before_resp.json()["status"] == "Triaged"

    # 2. Patch db.commit to raise an exception inside the transaction
    with patch.object(db_session, "commit", side_effect=RuntimeError("Simulated DB Disk Failure")):
        dec_resp = await client.post(
            f"/incidents/{inc_id}/decision",
            json={"decision": "Approved", "note": "Should fail and roll back"},
            headers=auth_headers,
        )
        assert dec_resp.status_code == 500
        assert "Database transaction failed and was rolled back" in dec_resp.json()["detail"]

    # 3. Verify atomic rollback:
    # a) Incident status must still be 'Triaged', NOT 'Approved'
    after_resp = await client.get(f"/incidents/{inc_id}", headers=auth_headers)
    assert after_resp.json()["status"] == "Triaged"

    # b) No decision row was inserted for this triage run
    dec_query = await db_session.execute(
        select(Decision).where(Decision.triage_run_id == triage_run_id)
    )
    decisions = dec_query.scalars().all()
    assert len(decisions) == 0, "Decision row was committed despite transaction failure!"
