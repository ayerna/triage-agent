"""
Pytest fixtures for the Incident Triage test suite.

Design:
- Uses dedicated triage_test_db to avoid wiping development seed data.
- NullPool engine per test eliminates cross-loop connection sharing in asyncpg.
- Automatic rate-limit shielding: all tests run offline with instant stubs,
  while test_live_groq_api_call uses the real live hosted Groq key.
"""
import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth import hash_password
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import User

ASYNC_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://triage_user:triage_pass@localhost:5432/triage_test_db",
)
SYNC_DB_URL = ASYNC_DB_URL.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(autouse=True)
def rate_limit_shield(request, monkeypatch):
    """
    Keep live GROQ_API_KEY for test_live_groq_api_call,
    and use offline stub for other unit tests to prevent exhausting Groq free-tier TPM.
    """
    if "test_live_groq_api_call" not in request.node.name:
        monkeypatch.setattr(settings, "GROQ_API_KEY", "")


# ---------------------------------------------------------------------------
# Session-scoped schema setup using SYNC engine (no loop issues)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def setup_test_schema():
    """Create all tables once per session using the synchronous psycopg2 driver."""
    sync_engine = create_engine(SYNC_DB_URL, echo=False)
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    yield
    sync_engine.dispose()


# ---------------------------------------------------------------------------
# Per-test async fixtures — NullPool = fresh connection every time
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def db_session():
    """
    Create a fresh async engine with NullPool for each test, yield a session,
    then clean up data between tests by truncating tables.
    """
    engine = create_async_engine(ASYNC_DB_URL, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())

    await engine.dispose()


@pytest_asyncio.fixture()
async def client(db_session: AsyncSession):
    """AsyncClient wired to the per-test session via dependency override."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture()
async def test_user(db_session: AsyncSession) -> User:
    user = User(
        email="test@example.com",
        hashed_password=hash_password("testpass123"),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture()
async def auth_token(client: AsyncClient, test_user: User) -> str:
    resp = await client.post(
        "/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest_asyncio.fixture()
async def auth_headers(auth_token: str) -> dict:
    return {"Authorization": f"Bearer {auth_token}"}
