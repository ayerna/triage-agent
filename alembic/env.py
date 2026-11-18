"""
Alembic env.py — wires our SQLAlchemy models into the migration engine.

WHY ALEMBIC: We never ALTER tables by hand. Every schema change is a versioned
migration file. That means the DB state is reproducible from scratch and changes
are tracked in Git alongside the code that needs them.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import Base so Alembic can compare models vs DB for autogenerate.
from app.database import Base
import app.models  # noqa: F401 — registers all ORM classes on Base.metadata

config = context.config

# Override sqlalchemy.url from environment variable if set.
# Alembic uses sync psycopg2 driver; swap asyncpg → psycopg2 in the URL.
db_url = os.getenv("DATABASE_URL", "")
if not db_url:
    # Fall back to alembic.ini value (already a sync URL)
    db_url = config.get_main_option("sqlalchemy.url") or ""

# Always ensure we use the sync psycopg2 URL for Alembic
sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
