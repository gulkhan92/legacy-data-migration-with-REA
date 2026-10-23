"""billing engine: plans, snapshot columns, one invoice per period, and the platform wide aggregate functions

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-25 09:00:00

* The three illustrative plans are inserted (Starter, Professional, Enterprise).
* daily_metrics gets the subscription revenue recognised that day, the number of paying organizations, the trailing 12 month net
  revenue retention and a JSON breakdown by plan, industry and region.
* An organization has at most one active subscription, a subscription is invoiced once per period, a job meters usage once.
* Revenue figures span organizations, and Row Level Security (rightly) keeps one organization from reading another. The platform wide
  numbers are therefore computed by SECURITY DEFINER functions owned by the migration role. They return aggregates, plus per
  customer rows in which only simulated organizations carry their name (a live organization is shown as "Live organization xxxxxx").
  They are the only way the application role sees across organizations, none of them returns a tenant's data rows, and
  EXECUTE is granted to the application role only. The migration role must bypass Row Level Security (a superuser does, which is
  the default of the Postgres image); the tests check that the functions really see every organization.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

PLANS = [
    ("starter", "Starter", 0, 100_000, "50.00"),
    ("professional", "Professional", 49_900, 2_000_000, "20.00"),
    ("enterprise", "Enterprise", 250_000, 25_000_000, "8.00"),
]

SEARCH_PATH = "SET search_path = public, pg_temp"
UTC_DATE = "(%s AT TIME ZONE 'UTC')::date"

ORG_STATE = f"""
CREATE OR REPLACE FUNCTION platform_org_state(d date)
RETURNS TABLE (org_id uuid, plan_code text, fee integer, industry text, region text, simulated boolean)
LANGUAGE sql STABLE SECURITY DEFINER {SEARCH_PATH} AS $$
    SELECT DISTINCT ON (s.org_id) s.org_id, p.code::text, p.monthly_fee_cents, o.industry::text, o.region::text, o.is_simulated
    FROM subscriptions s
    JOIN plans p ON p.id = s.plan_id
    JOIN organizations o ON o.id = s.org_id
    WHERE {UTC_DATE % 's.started_at'} <= d AND (s.ended_at IS NULL OR {UTC_DATE % 's.ended_at'} > d)
    ORDER BY s.org_id, s.started_at DESC
$$
"""

BILLABLE = f"""
CREATE OR REPLACE FUNCTION platform_billable_orgs(d date) RETURNS TABLE (org_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER {SEARCH_PATH} AS $$ SELECT org_id FROM platform_org_state(d) $$
"""

DAY_METRICS = f"""
CREATE OR REPLACE FUNCTION platform_day_metrics(d date) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER {SEARCH_PATH} AS $$
DECLARE
    result jsonb;
    year_ago date := (d - interval '12 months')::date;
BEGIN
    WITH today AS (SELECT * FROM platform_org_state(d)),
         prev AS (SELECT * FROM platform_org_state(d - 1)),
         mv AS (
             SELECT (p.org_id IS NOT NULL) AS was, (t.org_id IS NOT NULL) AS now_active, COALESCE(p.fee, 0) AS pf, COALESCE(t.fee, 0) AS tf
             FROM today t FULL OUTER JOIN prev p ON p.org_id = t.org_id
         ),
         base AS (SELECT org_id, fee FROM platform_org_state(year_ago) WHERE fee > 0),
         retention AS (
             SELECT sum(b.fee) AS start_mrr, sum(COALESCE(t.fee, 0)) AS now_mrr FROM base b LEFT JOIN today t ON t.org_id = b.org_id
         ),
         finished AS (
             SELECT count(*) AS n, count(*) FILTER (WHERE status = 'COMPLETED') AS ok
             FROM jobs WHERE status IN ('COMPLETED', 'FAILED') AND {UTC_DATE % 'completed_at'} = d
         ),
         usage AS (
             SELECT COALESCE(sum(records_loaded), 0) AS records, avg(duration_ms) AS avg_ms
             FROM usage_events WHERE {UTC_DATE % 'occurred_at'} = d
         ),
         llm AS (
             SELECT
                 COALESCE(sum(tokens_in + tokens_out) FILTER (WHERE provider = 'groq' AND status = 'ok' AND NOT cache_hit), 0) AS tokens_groq,
                 COALESCE(sum(tokens_in + tokens_out) FILTER (WHERE provider = 'gemini' AND status = 'ok' AND NOT cache_hit), 0) AS tokens_gemini,
                 count(*) FILTER (WHERE status IN ('rate_limited', 'timeout', 'error')) AS failovers,
                 count(*) FILTER (WHERE cache_hit) AS cache_hits
             FROM llm_calls WHERE {UTC_DATE % 'created_at'} = d
         ),
         quality AS (
             SELECT avg(quality_after) AS score FROM reconciliation_reports WHERE {UTC_DATE % 'created_at'} = d
         ),
         lines AS (
             SELECT il.kind::text AS kind, il.amount_cents, o.industry::text AS industry, o.region::text AS region, p.code::text AS plan_code
             FROM invoice_lines il
             JOIN invoices i ON i.id = il.invoice_id
             JOIN organizations o ON o.id = i.org_id
             LEFT JOIN subscriptions s ON s.id = i.subscription_id
             LEFT JOIN plans p ON p.id = s.plan_id
             WHERE i.status <> 'void' AND i.period_end = d
         )
    SELECT jsonb_build_object(
        'active_orgs', (SELECT count(*) FROM today),
        'paying_orgs', (SELECT count(*) FROM today WHERE fee > 0),
        'new_orgs', (SELECT count(*) FROM organizations WHERE {UTC_DATE % 'created_at'} = d),
        'mrr_cents', (SELECT COALESCE(sum(fee), 0) FROM today),
        'new_mrr_cents', (SELECT COALESCE(sum(tf), 0) FROM mv WHERE NOT was AND now_active),
        'expansion_mrr_cents', (SELECT COALESCE(sum(tf - pf), 0) FROM mv WHERE was AND now_active AND tf > pf),
        'contraction_mrr_cents', (SELECT COALESCE(sum(pf - tf), 0) FROM mv WHERE was AND now_active AND tf < pf),
        'churned_mrr_cents', (SELECT COALESCE(sum(pf), 0) FROM mv WHERE was AND NOT now_active),
        'subscription_revenue_cents', (SELECT COALESCE(sum(amount_cents), 0) FROM lines WHERE kind = 'base'),
        'usage_revenue_cents', (SELECT COALESCE(sum(amount_cents), 0) FROM lines WHERE kind = 'overage'),
        'jobs', (SELECT n FROM finished),
        'records', (SELECT records FROM usage),
        'tokens_groq', (SELECT tokens_groq FROM llm),
        'tokens_gemini', (SELECT tokens_gemini FROM llm),
        'failovers', (SELECT failovers FROM llm),
        'cache_hits', (SELECT cache_hits FROM llm),
        'success_rate', (SELECT CASE WHEN n > 0 THEN round(ok::numeric / n, 4) END FROM finished),
        'avg_duration_ms', (SELECT round(avg_ms) FROM usage),
        'quality_score', (SELECT round(score, 2) FROM quality),
        'nrr_12m', (SELECT CASE WHEN start_mrr > 0 THEN round(now_mrr::numeric / start_mrr, 4) END FROM retention),
        'is_simulated', EXISTS (SELECT 1 FROM today WHERE simulated),
        'breakdown', jsonb_build_object(
            'mrr_by_plan', COALESCE((SELECT jsonb_object_agg(k, v) FROM (SELECT plan_code AS k, sum(fee) AS v FROM today GROUP BY plan_code) x), '{{}}'::jsonb),
            'orgs_by_plan', COALESCE((SELECT jsonb_object_agg(k, v) FROM (SELECT plan_code AS k, count(*) AS v FROM today GROUP BY plan_code) x), '{{}}'::jsonb),
            'mrr_by_industry', COALESCE((SELECT jsonb_object_agg(k, v) FROM (SELECT COALESCE(industry, 'Unknown') AS k, sum(fee) AS v FROM today GROUP BY 1) x), '{{}}'::jsonb),
            'mrr_by_region', COALESCE((SELECT jsonb_object_agg(k, v) FROM (SELECT COALESCE(region, 'Unknown') AS k, sum(fee) AS v FROM today GROUP BY 1) x), '{{}}'::jsonb),
            'usage_by_plan', COALESCE((SELECT jsonb_object_agg(k, v) FROM (SELECT COALESCE(plan_code, 'unknown') AS k, sum(amount_cents) AS v FROM lines WHERE kind = 'overage' GROUP BY 1) x), '{{}}'::jsonb),
            'usage_by_industry', COALESCE((SELECT jsonb_object_agg(k, v) FROM (SELECT COALESCE(industry, 'Unknown') AS k, sum(amount_cents) AS v FROM lines WHERE kind = 'overage' GROUP BY 1) x), '{{}}'::jsonb),
            'usage_by_region', COALESCE((SELECT jsonb_object_agg(k, v) FROM (SELECT COALESCE(region, 'Unknown') AS k, sum(amount_cents) AS v FROM lines WHERE kind = 'overage' GROUP BY 1) x), '{{}}'::jsonb)
        )
    ) INTO result;
    RETURN result;
END
$$
"""

MASKED = "CASE WHEN o.is_simulated THEN o.name ELSE 'Live organization ' || left(md5(o.id::text), 6) END"

TOP_CUSTOMERS = f"""
CREATE OR REPLACE FUNCTION platform_top_customers(as_of date, max_rows integer) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER {SEARCH_PATH} AS $$
    WITH st AS (SELECT * FROM platform_org_state(as_of)),
         months AS (
             SELECT generate_series(date_trunc('month', as_of::timestamp) - interval '11 months', date_trunc('month', as_of::timestamp), interval '1 month')::date AS m
         ),
         rev AS (
             SELECT i.org_id, date_trunc('month', i.period_end)::date AS m, sum(i.total_cents) AS cents
             FROM invoices i
             WHERE i.status <> 'void' AND i.period_end <= as_of AND i.period_end >= (SELECT min(m) FROM months)
             GROUP BY 1, 2
         ),
         tot AS (SELECT org_id, sum(cents) AS revenue FROM rev GROUP BY org_id),
         top AS (
             SELECT st.*, COALESCE(tot.revenue, 0) AS revenue
             FROM st LEFT JOIN tot ON tot.org_id = st.org_id
             ORDER BY COALESCE(tot.revenue, 0) DESC, st.fee DESC, st.org_id
             LIMIT GREATEST(max_rows, 0)
         )
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'name', {MASKED}, 'industry', top.industry, 'region', top.region, 'plan', top.plan_code, 'simulated', top.simulated,
               'mrr_cents', top.fee, 'revenue_12m_cents', top.revenue,
               'trend', (SELECT jsonb_agg(COALESCE(r.cents, 0) ORDER BY mo.m) FROM months mo LEFT JOIN rev r ON r.org_id = top.org_id AND r.m = mo.m)
           ) ORDER BY top.revenue DESC, top.fee DESC, top.org_id), '[]'::jsonb)
    FROM top JOIN organizations o ON o.id = top.org_id
$$
"""

INVOICE_PAGE = f"""
CREATE OR REPLACE FUNCTION platform_invoice_page(p_status text, p_q text, p_from date, p_to date, p_limit integer, p_offset integer) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER {SEARCH_PATH} AS $$
    WITH f AS (
        SELECT i.id, ({MASKED}) AS customer, o.industry::text AS industry, o.region::text AS region, p.code::text AS plan_code, i.period_start, i.period_end,
               i.status::text AS status, i.total_cents, i.issued_at, i.due_at, i.paid_at
        FROM invoices i
        JOIN organizations o ON o.id = i.org_id
        LEFT JOIN subscriptions s ON s.id = i.subscription_id
        LEFT JOIN plans p ON p.id = s.plan_id
        WHERE (p_status IS NULL OR i.status::text = p_status)
          AND (p_from IS NULL OR i.period_end >= p_from)
          AND (p_to IS NULL OR i.period_end <= p_to)
    ),
    g AS (SELECT * FROM f WHERE p_q IS NULL OR customer ILIKE '%' || p_q || '%'),
    page AS (SELECT * FROM g ORDER BY period_end DESC, issued_at DESC, id LIMIT GREATEST(p_limit, 0) OFFSET GREATEST(p_offset, 0))
    SELECT jsonb_build_object(
        'total', (SELECT count(*) FROM g),
        'total_cents', (SELECT COALESCE(sum(total_cents), 0) FROM g WHERE status <> 'void'),
        'items', COALESCE((SELECT jsonb_agg(to_jsonb(page) ORDER BY page.period_end DESC, page.issued_at DESC, page.id) FROM page), '[]'::jsonb)
    )
$$
"""

REVENUE_TOTALS = f"""
CREATE OR REPLACE FUNCTION platform_revenue_totals(p_from date, p_to date) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER {SEARCH_PATH} AS $$
    SELECT jsonb_build_object(
        'invoices', (SELECT count(*) FROM invoices WHERE status <> 'void' AND period_end BETWEEN p_from AND p_to),
        'invoice_total_cents', (SELECT COALESCE(sum(total_cents), 0) FROM invoices WHERE status <> 'void' AND period_end BETWEEN p_from AND p_to),
        'base_cents', (SELECT COALESCE(sum(il.amount_cents), 0) FROM invoice_lines il JOIN invoices i ON i.id = il.invoice_id
                       WHERE i.status <> 'void' AND i.period_end BETWEEN p_from AND p_to AND il.kind = 'base'),
        'overage_cents', (SELECT COALESCE(sum(il.amount_cents), 0) FROM invoice_lines il JOIN invoices i ON i.id = il.invoice_id
                          WHERE i.status <> 'void' AND i.period_end BETWEEN p_from AND p_to AND il.kind = 'overage')
    )
$$
"""

MARK_OVERDUE = f"""
CREATE OR REPLACE FUNCTION platform_mark_overdue(as_of date) RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER {SEARCH_PATH} AS $$
DECLARE changed integer;
BEGIN
    UPDATE invoices SET status = 'overdue' WHERE status = 'open' AND due_at IS NOT NULL AND (due_at AT TIME ZONE 'UTC')::date < as_of;
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed;
END
$$
"""

EXPORTED = [
    ("platform_billable_orgs(date)", BILLABLE), ("platform_day_metrics(date)", DAY_METRICS), ("platform_top_customers(date, integer)", TOP_CUSTOMERS),
    ("platform_invoice_page(text, text, date, date, integer, integer)", INVOICE_PAGE), ("platform_revenue_totals(date, date)", REVENUE_TOTALS),
    ("platform_mark_overdue(date)", MARK_OVERDUE),
]  # fmt: skip


def upgrade() -> None:
    for code, name, fee, included, overage in PLANS:
        op.execute(
            sa.text(
                "INSERT INTO plans (id, code, name, monthly_fee_cents, included_records, overage_cents_per_1000, is_active) "
                "VALUES (gen_random_uuid(), :code, :name, :fee, CAST(:included AS bigint), CAST(:overage AS numeric), true) ON CONFLICT (code) DO NOTHING"
            ).bindparams(code=code, name=name, fee=fee, included=included, overage=overage)
        )

    op.add_column("daily_metrics", sa.Column("subscription_revenue_cents", sa.BigInteger(), server_default="0", nullable=False))
    op.add_column("daily_metrics", sa.Column("paying_orgs", sa.Integer(), server_default="0", nullable=False))
    op.add_column("daily_metrics", sa.Column("nrr_12m", sa.Numeric(8, 4), nullable=True))
    op.add_column("daily_metrics", sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False))

    op.create_index("uq_subscriptions_one_active_per_org", "subscriptions", ["org_id"], unique=True, postgresql_where=sa.text("ended_at IS NULL"))
    op.create_index("uq_invoices_subscription_period", "invoices", ["subscription_id", "period_start"], unique=True)
    op.create_index("uq_usage_events_job", "usage_events", ["job_id"], unique=True, postgresql_where=sa.text("job_id IS NOT NULL"))
    op.create_index("ix_invoices_period_end", "invoices", ["period_end"])

    op.execute(ORG_STATE)
    op.execute("REVOKE ALL ON FUNCTION platform_org_state(date) FROM PUBLIC")  # internal: per organization rows
    role = get_settings().app_db_user
    for signature, ddl in EXPORTED:
        op.execute(ddl)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {role}")


def downgrade() -> None:
    for signature, _ in reversed(EXPORTED):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP FUNCTION IF EXISTS platform_org_state(date)")
    op.drop_index("ix_invoices_period_end", table_name="invoices")
    op.drop_index("uq_usage_events_job", table_name="usage_events")
    op.drop_index("uq_invoices_subscription_period", table_name="invoices")
    op.drop_index("uq_subscriptions_one_active_per_org", table_name="subscriptions")
    for column in ("breakdown", "nrr_12m", "paying_orgs", "subscription_revenue_cents"):
        op.drop_column("daily_metrics", column)
    op.execute("DELETE FROM plans WHERE code IN ('starter', 'professional', 'enterprise') AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.plan_id = plans.id)")
