"""
Pytest fixtures for Slice 1.

Design: each test gets its own AsyncEngine with NullPool so no connections
are shared across event loops. pytest-asyncio creates a new loop per test
in function scope; NullPool means each test always gets a brand-new
asyncpg connection and there is no cross-loop contamination.

We create the schema once at session start (using a separate synchronous
psycopg2 engine so we avoid the loop issue entirely for setup), then each
test creates/tears down its data via the per-test async session.
"""
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth import hash_password
from app.database import Base, get_db
from app.main import app
from app.models import User

ASYNC_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://triage_user:triage_pass@localhost:5432/triage_db",
)
SYNC_DB_URL = ASYNC_DB_URL.replace("postgresql+asyncpg://", "postgresql://")


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
    then drop/recreate all tables for the next test.
    NullPool ensures no asyncpg connection is ever reused across event loops.
    """
    engine = create_async_engine(ASYNC_DB_URL, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    # Clean up data between tests by truncating — faster than drop/create
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
