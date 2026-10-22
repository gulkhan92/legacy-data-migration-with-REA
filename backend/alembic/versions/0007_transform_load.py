"""transform rules, one load per job, quarantine index

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-24 09:00:00

* transform_rules: derived rules have no mapping, so mapping_id becomes optional; a rule records its target field, its source
  columns, its status (ready, needs_input, failed), where it came from, the failures a dry run found, and notes.
* Every warehouse table gets a unique (job_id, source_key): a load is idempotent by job id, and the database refuses a second
  copy of a legacy record.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

WAREHOUSE_TABLES = ["customers", "products", "orders", "order_items", "patients", "encounters", "conditions", "medications", "observations"]


def upgrade() -> None:
    op.alter_column("transform_rules", "mapping_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.add_column("transform_rules", sa.Column("target_field_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("transform_rules", sa.Column("target_table", sa.String(length=100), nullable=False, server_default=""))
    op.add_column("transform_rules", sa.Column("target_field", sa.String(length=100), nullable=False, server_default=""))
    op.add_column("transform_rules", sa.Column("source_table", sa.String(length=255), nullable=True))
    op.add_column("transform_rules", sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False))
    op.add_column("transform_rules", sa.Column("status", sa.String(length=20), server_default="ready", nullable=False))
    op.add_column("transform_rules", sa.Column("origin", sa.String(length=20), server_default="deterministic", nullable=False))
    op.add_column("transform_rules", sa.Column("failures", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False))
    op.add_column("transform_rules", sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False))
    op.create_foreign_key("fk_transform_rules_target_field_id_target_fields", "transform_rules", "target_fields", ["target_field_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_transform_rules_target_field_id", "transform_rules", ["target_field_id"])
    op.create_check_constraint("ck_transform_rules_status", "transform_rules", "status IN ('ready', 'needs_input', 'failed')")
    op.create_index("uq_transform_rules_job_target", "transform_rules", ["job_id", "target_table", "target_field"], unique=True)
    op.create_index("ix_quarantined_rows_job_id_reason_code", "quarantined_rows", ["job_id", "reason_code"])
    for table in WAREHOUSE_TABLES:
        op.create_index(f"uq_{table}_job_source_key", table, ["job_id", "source_key"], unique=True, schema="warehouse")


def downgrade() -> None:
    for table in WAREHOUSE_TABLES:
        op.drop_index(f"uq_{table}_job_source_key", table_name=table, schema="warehouse")
    op.drop_index("ix_quarantined_rows_job_id_reason_code", table_name="quarantined_rows")
    op.drop_index("uq_transform_rules_job_target", table_name="transform_rules")
    op.drop_constraint("ck_transform_rules_status", "transform_rules", type_="check")
    op.drop_index("ix_transform_rules_target_field_id", table_name="transform_rules")
    op.drop_constraint("fk_transform_rules_target_field_id_target_fields", "transform_rules", type_="foreignkey")
    for column in ("notes", "failures", "origin", "status", "inputs", "source_table", "target_field", "target_table", "target_field_id"):
        op.drop_column("transform_rules", column)
    op.execute("DELETE FROM transform_rules WHERE mapping_id IS NULL")
    op.alter_column("transform_rules", "mapping_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
