"""mapping decision details

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-22 09:00:00

A mapping row records how the engine decided (memory, auto, needs_llm, review, unmapped), the top candidate fields with
their similarities, and the reason, so a reviewer sees why a column was or was not accepted.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mappings", sa.Column("decision", sa.String(length=20), server_default="auto", nullable=False))
    op.add_column("mappings", sa.Column("candidates", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False))
    op.add_column("mappings", sa.Column("rationale", sa.Text(), nullable=True))
    op.add_column("mappings", sa.Column("margin", sa.Numeric(precision=5, scale=4), nullable=True))
    op.create_check_constraint(
        "ck_mappings_decision", "mappings", "decision IN ('memory', 'auto', 'needs_llm', 'review', 'unmapped')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_mappings_decision", "mappings", type_="check")
    op.drop_column("mappings", "margin")
    op.drop_column("mappings", "rationale")
    op.drop_column("mappings", "candidates")
    op.drop_column("mappings", "decision")
