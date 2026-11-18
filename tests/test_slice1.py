"""
Slice 1 tests — covers 200, 400, 401, 404 for all endpoints.
Run with: pytest tests/test_slice1.py -v
"""
import pytest


# ── Health ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_200(client, test_user):
    resp = await client.post(
        "/auth/login", json={"email": "test@example.com", "password": "testpass123"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_401_wrong_password(client, test_user):
    resp = await client.post(
        "/auth/login", json={"email": "test@example.com", "password": "wrongpass"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_401_unknown_user(client):
    resp = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "pass"}
    )
    assert resp.status_code == 401


# ── Incidents — auth guard ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_incidents_401_no_token(client):
    resp = await client.get("/incidents")
    assert resp.status_code == 403  # HTTPBearer returns 403 when header is missing


@pytest.mark.asyncio
async def test_list_incidents_401_bad_token(client):
    resp = await client.get("/incidents", headers={"Authorization": "Bearer bad.token.here"})
    assert resp.status_code == 401


# ── Incidents — CRUD ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_incident_201(client, auth_headers):
    resp = await client.post(
        "/incidents",
        json={"title": "Test Engine Fault", "description": "Main engine temperature high"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Test Engine Fault"
    assert data["status"] == "Open"
    assert data["severity"] is None


@pytest.mark.asyncio
async def test_create_incident_400_missing_title(client, auth_headers):
    resp = await client.post(
        "/incidents",
        json={"title": "", "description": "Some description"},
        headers=auth_headers,
    )
    assert resp.status_code == 422  # Pydantic validation error


@pytest.mark.asyncio
async def test_create_incident_400_missing_description(client, auth_headers):
    resp = await client.post(
        "/incidents",
        json={"title": "Some title", "description": "   "},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_incident_400_missing_fields(client, auth_headers):
    resp = await client.post(
        "/incidents",
        json={"title": "Only title"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_incident_200(client, auth_headers):
    # Create first
    create_resp = await client.post(
        "/incidents",
        json={"title": "Radar Fault", "description": "X-band radar offline"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    incident_id = create_resp.json()["id"]

    # Fetch it
    resp = await client.get(f"/incidents/{incident_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == incident_id


@pytest.mark.asyncio
async def test_get_incident_404(client, auth_headers):
    resp = await client.get("/incidents/999999", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_incidents_200(client, auth_headers):
    # Create a couple
    for i in range(2):
        await client.post(
            "/incidents",
            json={"title": f"Fault {i}", "description": f"Description {i}"},
            headers=auth_headers,
        )
    resp = await client.get("/incidents", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 2


# ── Triage + Decision placeholders ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_triage_404_unknown_incident(client, auth_headers):
    resp = await client.post("/incidents/999999/triage", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_triage_now_implemented(client, auth_headers):
    create_resp = await client.post(
        "/incidents",
        json={"title": "Oil Leak", "description": "Lube oil leak on aux engine"},
        headers=auth_headers,
    )
    incident_id = create_resp.json()["id"]
    resp = await client.post(f"/incidents/{incident_id}/triage", headers=auth_headers)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_decision_404_unknown_incident(client, auth_headers):
    resp = await client.post(
        "/incidents/999999/decision",
        json={"decision": "Approved"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_decision_422_invalid_decision(client, auth_headers):
    create_resp = await client.post(
        "/incidents",
        json={"title": "Fan Fault", "description": "Cargo hold fan tripped"},
        headers=auth_headers,
    )
    incident_id = create_resp.json()["id"]
    resp = await client.post(
        f"/incidents/{incident_id}/decision",
        json={"decision": "MAYBE"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
