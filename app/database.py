"""
SQLAlchemy async database engine and session factory.
We use asyncpg driver so every DB call is non-blocking inside FastAPI's event loop.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:  # type: ignore[override]
    """FastAPI dependency: yields one async DB session per request."""
    async with AsyncSessionLocal() as session:
        yield session
