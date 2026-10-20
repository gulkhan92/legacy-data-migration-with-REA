"""second source file for jobs whose source has two parts

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20 09:00:00

A .NET application and its data file are uploaded as two sources. The job points at the assembly with
source_id and at the data file with data_source_id.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_jobs_data_source_id_sources", "jobs", "sources", ["data_source_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_jobs_data_source_id", "jobs", ["data_source_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_data_source_id", table_name="jobs")
    op.drop_constraint("fk_jobs_data_source_id_sources", "jobs", type_="foreignkey")
    op.drop_column("jobs", "data_source_id")
