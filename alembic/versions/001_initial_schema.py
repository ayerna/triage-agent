"""
Migration 001 — initial schema: users + incidents tables.
triage_runs and decisions are added in Migration 002 (Slice 2).

WHY THIS MIGRATION EXISTS: Alembic gives us a reproducible, version-controlled
way to build the database schema. Running 'alembic upgrade head' on a fresh DB
produces the exact same tables that code expects — no manual SQL needed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Create PostgreSQL ENUM types safely --
    # PostgreSQL has no CREATE TYPE IF NOT EXISTS syntax.
    # We use a DO block to check pg_type first — this is the standard idiom.
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'severity_enum') THEN
                CREATE TYPE severity_enum AS ENUM ('Low', 'Medium', 'High', 'Critical');
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'status_enum') THEN
                CREATE TYPE status_enum AS ENUM ('Open', 'Triaged', 'Approved', 'Rejected');
            END IF;
        END $$;
    """)

    # -- users --
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_id", "users", ["id"])

    # -- incidents --
    # Use postgresql.ENUM with create_type=False so no duplicate CREATE TYPE.
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "severity",
            postgresql.ENUM("Low", "Medium", "High", "Critical", name="severity_enum", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM("Open", "Triaged", "Approved", "Rejected", name="status_enum", create_type=False),
            nullable=False,
            server_default="Open",
        ),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_incidents_id", "incidents", ["id"])


def downgrade() -> None:
    op.drop_table("incidents")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS status_enum")
    op.execute("DROP TYPE IF EXISTS severity_enum")
