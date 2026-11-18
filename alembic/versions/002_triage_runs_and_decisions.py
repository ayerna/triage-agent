"""
Migration 002 — add category to incidents, create triage_runs and decisions tables.

WHY THIS MIGRATION: Enables auditability for every triage run and human decision.
The category column captures the triaged classification, triage_runs persists the full
agent proposal + critic checks, and decisions captures the human approval/rejection.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_triage_runs_and_decisions"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add one column 'category' to incidents
    op.add_column("incidents", sa.Column("category", sa.String(50), nullable=True))

    # 2. Create triage_runs table
    op.create_table(
        "triage_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposal_json", sa.Text(), nullable=True),
        sa.Column("steps_json", sa.Text(), nullable=True),
        sa.Column("rule_check_json", sa.Text(), nullable=True),
        sa.Column("critic_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_triage_runs_id", "triage_runs", ["id"])
    op.create_index("ix_triage_runs_incident_id", "triage_runs", ["incident_id"])

    # 3. Create decisions table
    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("triage_run_id", sa.Integer(), sa.ForeignKey("triage_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),  # Approved / Rejected
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_decisions_id", "decisions", ["id"])
    op.create_index("ix_decisions_triage_run_id", "decisions", ["triage_run_id"])


def downgrade() -> None:
    op.drop_table("decisions")
    op.drop_table("triage_runs")
    op.drop_column("incidents", "category")
