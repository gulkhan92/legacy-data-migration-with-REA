"""console support: a retention setting, and the platform wide functions behind the operations and cohort charts

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27 09:00:00

* org_settings.retention_days: how long the encrypted copy of an uploaded file is kept after its jobs have finished. A nightly job
  deletes older copies (the migrated data, the reports and the audit log are not touched).
* platform_operations_detail(from, to): the numbers behind the operations dashboard that the daily snapshot does not hold: how the
  columns were mapped (memory, embedding, model, human), the latency of every pipeline stage (p50 and p95, read from the stage events
  of the jobs), why rows were quarantined and which stages failed, the discovery confidence histogram, and the model call figures.
* platform_cohort_retention(as_of): organizations grouped by the month of their first subscription, and how many of them (and how
  much of their MRR) are still there each month after.
* platform_orgs_with_uploads(): which organizations to run the retention job for.

Like the functions of 0008 these are SECURITY DEFINER, return aggregates only, and are executable by the application role alone.
"""
import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

SEARCH_PATH = "SET search_path = public, pg_temp"
DAY = "(%s AT TIME ZONE 'UTC')::date"

OPERATIONS_DETAIL = f"""
CREATE OR REPLACE FUNCTION platform_operations_detail(p_from date, p_to date) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER {SEARCH_PATH} AS $$
DECLARE result jsonb;
BEGIN
    WITH
    started AS (
        SELECT job_id, substring(message from '^Stage ([a-z_]+) started') AS stage, min(created_at) AS t
        FROM job_events WHERE message ~ '^Stage [a-z_]+ started' AND {DAY % 'created_at'} BETWEEN p_from AND p_to GROUP BY 1, 2
    ),
    finished AS (
        SELECT job_id, substring(message from '^Stage ([a-z_]+) finished') AS stage, min(created_at) AS t
        FROM job_events WHERE message ~ '^Stage [a-z_]+ finished' AND {DAY % 'created_at'} BETWEEN p_from AND p_to + 1 GROUP BY 1, 2
    ),
    lat AS (
        SELECT s.stage, extract(epoch FROM (f.t - s.t)) AS secs FROM started s JOIN finished f ON f.job_id = s.job_id AND f.stage = s.stage WHERE f.t >= s.t
    ),
    maps AS (SELECT method::text AS k, count(*) AS n FROM mappings WHERE {DAY % 'created_at'} BETWEEN p_from AND p_to GROUP BY 1),
    quarantine AS (SELECT reason_code AS k, count(*) AS n FROM quarantined_rows WHERE {DAY % 'created_at'} BETWEEN p_from AND p_to GROUP BY 1),
    failed AS (
        SELECT detail->>'stage' AS k, count(*) AS n FROM job_events
        WHERE to_status = 'FAILED' AND detail ? 'stage' AND {DAY % 'created_at'} BETWEEN p_from AND p_to GROUP BY 1
    ),
    conf AS (
        SELECT width_bucket(discovery_confidence::numeric, 0, 1.0001, 10) AS b, count(*) AS n FROM source_columns
        WHERE discovery_confidence IS NOT NULL AND {DAY % 'created_at'} BETWEEN p_from AND p_to GROUP BY 1
    ),
    calls AS (
        SELECT count(*) FILTER (WHERE status = 'ok' AND NOT cache_hit) AS made, count(*) FILTER (WHERE cache_hit) AS cache_hits,
               count(*) FILTER (WHERE status IN ('rate_limited', 'timeout', 'error')) AS failed,
               COALESCE(sum(tokens_in + tokens_out) FILTER (WHERE status = 'ok' AND NOT cache_hit), 0) AS tokens
        FROM llm_calls WHERE {DAY % 'created_at'} BETWEEN p_from AND p_to
    )
    SELECT jsonb_build_object(
        'mapping_methods', COALESCE((SELECT jsonb_object_agg(k, n) FROM maps), '{{}}'::jsonb),
        'stage_latency', COALESCE((
            SELECT jsonb_object_agg(stage, jsonb_build_object('p50', round(p50::numeric, 2), 'p95', round(p95::numeric, 2), 'count', n))
            FROM (SELECT stage, percentile_cont(0.5) WITHIN GROUP (ORDER BY secs) AS p50, percentile_cont(0.95) WITHIN GROUP (ORDER BY secs) AS p95, count(*) AS n FROM lat GROUP BY stage) x
        ), '{{}}'::jsonb),
        'quarantine_reasons', COALESCE((SELECT jsonb_object_agg(k, n) FROM quarantine), '{{}}'::jsonb),
        'failed_stages', COALESCE((SELECT jsonb_object_agg(k, n) FROM failed), '{{}}'::jsonb),
        'discovery_confidence', (SELECT jsonb_agg(COALESCE((SELECT n FROM conf WHERE b = g), 0) ORDER BY g) FROM generate_series(1, 10) g),
        'llm', (SELECT jsonb_build_object('calls_made', made, 'cache_hits', cache_hits, 'failed_attempts', failed, 'tokens', tokens) FROM calls),
        'tokens_saved_by_cache', (SELECT COALESCE(sum(tokens * hits), 0) FROM llm_cache)
    ) INTO result;
    RETURN result;
END
$$
"""

COHORTS = f"""
CREATE OR REPLACE FUNCTION platform_cohort_retention(as_of date) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER {SEARCH_PATH} AS $$
    WITH firsts AS (
        SELECT org_id, date_trunc('month', min({DAY % 'started_at'}))::date AS cohort FROM subscriptions GROUP BY org_id
    ),
    grid AS (
        SELECT f.org_id, f.cohort, k.k, LEAST((f.cohort + ((k.k + 1) || ' months')::interval - interval '1 day')::date, as_of) AS md
        FROM firsts f CROSS JOIN generate_series(0, 23) AS k(k)
        WHERE (f.cohort + (k.k || ' months')::interval)::date <= as_of
    ),
    state AS (
        SELECT g.cohort, g.k, g.org_id,
               (SELECT p.monthly_fee_cents FROM subscriptions s JOIN plans p ON p.id = s.plan_id
                WHERE s.org_id = g.org_id AND {DAY % 's.started_at'} <= g.md AND (s.ended_at IS NULL OR {DAY % 's.ended_at'} > g.md)
                ORDER BY s.started_at DESC LIMIT 1) AS fee
        FROM grid g
    ),
    per AS (
        SELECT cohort, k, count(*) AS size, count(fee) AS active, COALESCE(sum(fee), 0) AS mrr FROM state GROUP BY cohort, k
    ),
    base AS (SELECT cohort, size, mrr AS mrr0 FROM per WHERE k = 0)
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'month', to_char(b.cohort, 'YYYY-MM'), 'size', b.size,
        'logos', (SELECT jsonb_agg(round(100.0 * p.active / b.size, 1) ORDER BY p.k) FROM per p WHERE p.cohort = b.cohort),
        'mrr', (SELECT jsonb_agg(CASE WHEN b.mrr0 > 0 THEN round(100.0 * p.mrr / b.mrr0, 1) END ORDER BY p.k) FROM per p WHERE p.cohort = b.cohort)
    ) ORDER BY b.cohort), '[]'::jsonb)
    FROM base b
$$
"""

ORGS_WITH_UPLOADS = f"""
CREATE OR REPLACE FUNCTION platform_orgs_with_uploads() RETURNS TABLE (org_id uuid, retention_days integer)
LANGUAGE sql STABLE SECURITY DEFINER {SEARCH_PATH} AS $$
    SELECT DISTINCT s.org_id, COALESCE(os.retention_days, 365) FROM sources s LEFT JOIN org_settings os ON os.org_id = s.org_id WHERE s.storage_path IS NOT NULL
$$
"""

USER_ORGS = f"""
CREATE OR REPLACE FUNCTION platform_user_organizations(p_user_id uuid) RETURNS TABLE (org_id uuid, name text, role text)
LANGUAGE sql STABLE SECURITY DEFINER {SEARCH_PATH} AS $$
    SELECT m.org_id, o.name::text, m.role::text FROM memberships m JOIN organizations o ON o.id = m.org_id WHERE m.user_id = p_user_id ORDER BY m.created_at
$$
"""

EXPORTED = [
    ("platform_operations_detail(date, date)", OPERATIONS_DETAIL), ("platform_cohort_retention(date)", COHORTS), ("platform_orgs_with_uploads()", ORGS_WITH_UPLOADS), ("platform_user_organizations(uuid)", USER_ORGS),
]


def upgrade() -> None:
    op.add_column("org_settings", sa.Column("retention_days", sa.Integer(), server_default="365", nullable=False))
    op.create_check_constraint("ck_org_settings_retention_days", "org_settings", "retention_days BETWEEN 7 AND 3650")
    role = get_settings().app_db_user
    for signature, ddl in EXPORTED:
        op.execute(ddl)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {role}")


def downgrade() -> None:
    for signature, _ in reversed(EXPORTED):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.drop_constraint("ck_org_settings_retention_days", "org_settings", type_="check")
    op.drop_column("org_settings", "retention_days")
