"""Analytics: revenue (admin) and operations (analyst and admin), over the daily snapshots.

Every endpoint takes a date range (`from`, `to`, default the last 90 days of data), a granularity (day, week, month) and, where it makes
sense, `compare=true` for the period of the same length before it. Answers are cached for a minute (Redis). CSV exports share the queries.
Revenue spans organizations, so only simulated customers carry their names; a live organization is shown as "Live organization xxxxxx".
"""

import csv
import io
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_tenant_db, require_role
from app.billing import analytics, cache
from app.billing.analytics import Granularity
from app.core.security import Principal
from app.db.models.enums import InvoiceStatus, Role

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

MAX_SPAN_DAYS = 800
NO_DATA = "There are no snapshots in this range. They are taken every night (python -m app.billing.cli snapshot)."
admin_only = require_role()
operations_roles = require_role(Role.ANALYST)


class Range:
    """Shared query parameters. Defaults to the last 90 days of data."""

    def __init__(
        self,
        date_from: date | None = Query(None, alias="from", description="First day (default: 89 days before `to`)"),
        date_to: date | None = Query(None, alias="to", description="Last day (default: the latest snapshot)"),
        granularity: Granularity = Query("day", description="Bucket size of the series"),
        compare: bool = Query(False, description="Also return the previous period of the same length and the change"),
        days: int | None = Query(None, ge=1, le=MAX_SPAN_DAYS, description="The last N days up to `to` (default: the latest snapshot); ignored when `from` is given"),
    ) -> None:
        self.date_from, self.date_to, self.granularity, self.compare, self.days = date_from, date_to, granularity, compare, days

    def resolve(self, db: Session) -> tuple[date, date]:
        start, end = analytics.default_range(db, self.date_from, self.date_to, default_days=self.days or 90)
        if start > end:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="`from` must not be after `to`")
        if (end - start).days + 1 > MAX_SPAN_DAYS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"The range is limited to {MAX_SPAN_DAYS} days")
        return start, end


def _run(name: str, params: dict[str, Any], compute) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    def safe() -> dict[str, Any]:
        try:
            return compute()  # type: ignore[no-any-return]
        except analytics.NoData:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_DATA) from None

    return cache.cached(name, params, safe)


def _revenue(db: Session, r: Range) -> dict[str, Any]:
    start, end = r.resolve(db)
    return _run("revenue", {"a": start, "b": end, "g": r.granularity, "c": r.compare}, lambda: analytics.revenue_report(db, start, end, r.granularity, r.compare))


def _operations(db: Session, r: Range) -> dict[str, Any]:
    start, end = r.resolve(db)
    return _run("operations", {"a": start, "b": end, "g": r.granularity, "c": r.compare}, lambda: analytics.operations_report(db, start, end, r.granularity, r.compare))


@router.get("/revenue", summary="Revenue: MRR, movements, revenue, retention (admin)")
def revenue(r: Range = Depends(), _: Principal = Depends(admin_only), db: Session = Depends(get_tenant_db, scope="function")) -> dict[str, Any]:
    """Series per bucket and KPIs for the range, the previous period and the change with `compare=true`, and `checks` that say whether the MRR movements add up
    and whether the revenue shown equals the invoices of the range."""
    return _revenue(db, r)


@router.get("/revenue/breakdown", summary="MRR and usage revenue by plan, industry or region (admin)")
def revenue_breakdown(
    dimension: str = Query("plan", pattern="^(plan|industry|region)$"), r: Range = Depends(), _: Principal = Depends(admin_only), db: Session = Depends(get_tenant_db, scope="function")
) -> dict[str, Any]:
    start, end = r.resolve(db)
    return _run("breakdown", {"d": dimension, "a": start, "b": end, "g": r.granularity}, lambda: analytics.breakdown_report(db, dimension, start, end, r.granularity))


@router.get("/customers/top", summary="The customers with the most revenue over the last 12 months (admin)")
def customers_top(
    limit: int = Query(10, ge=1, le=50), as_of: date | None = Query(None, description="Default: the latest snapshot"),
    _: Principal = Depends(admin_only), db: Session = Depends(get_tenant_db, scope="function"),
) -> dict[str, Any]:
    day = as_of or analytics.latest_date(db) or date.today()
    return _run("customers", {"d": day, "n": limit}, lambda: analytics.top_customers(db, day, limit))


@router.get("/invoices", summary="Invoices of all customers, filtered and paginated (admin)")
def invoices(
    invoice_status: InvoiceStatus | None = Query(None, alias="status"), q: str | None = Query(None, max_length=100, description="Part of the customer name"),
    date_from: date | None = Query(None, alias="from", description="Invoices whose period ends on or after this day"), date_to: date | None = Query(None, alias="to"),
    limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0), _: Principal = Depends(admin_only), db: Session = Depends(get_tenant_db, scope="function"),
) -> dict[str, Any]:
    params = {"s": invoice_status.value if invoice_status else None, "q": q, "a": date_from, "b": date_to, "l": limit, "o": offset}
    return _run("invoices", params, lambda: analytics.invoice_page(db, params["s"], q, date_from, date_to, limit, offset))


@router.get("/operations", summary="Jobs, success rate, duration, tokens by provider, failovers, cache hits, quality (analyst and admin)")
def operations(r: Range = Depends(), _: Principal = Depends(operations_roles), db: Session = Depends(get_tenant_db, scope="function")) -> dict[str, Any]:
    return _operations(db, r)


@router.get("/operations/detail", summary="Mapping methods, stage latency, errors, discovery confidence and model figures (analyst and admin)")
def operations_detail(r: Range = Depends(), _: Principal = Depends(operations_roles), db: Session = Depends(get_tenant_db, scope="function")) -> dict[str, Any]:
    start, end = r.resolve(db)
    return _run("operations-detail", {"a": start, "b": end}, lambda: analytics.operations_detail(db, start, end))


@router.get("/revenue/cohorts", summary="Retention of customers by the month they signed up (admin)")
def revenue_cohorts(as_of: date | None = Query(None, description="Default: the latest snapshot"), _: Principal = Depends(admin_only), db: Session = Depends(get_tenant_db, scope="function")) -> dict[str, Any]:
    day = as_of or analytics.latest_date(db) or date.today()
    return _run("cohorts", {"d": day}, lambda: analytics.cohort_retention(db, day))


# ------------------------------------------------------------------ CSV


def _safe(value: Any) -> Any:
    """A text cell that a spreadsheet would read as a formula gets a quote in front."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return "" if value is None else value


def _csv(filename: str, columns: list[str], rows: list[dict[str, Any]]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_safe(row.get(c)) for c in columns])
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/revenue.csv", summary="Revenue series as CSV (admin)", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def revenue_csv(r: Range = Depends(), _: Principal = Depends(admin_only), db: Session = Depends(get_tenant_db, scope="function")) -> Response:
    report = _revenue(db, Range(r.date_from, r.date_to, r.granularity, False, r.days))
    return _csv(f"revenue-{report['range']['from']}-{report['range']['to']}-{r.granularity}.csv", ["period", *analytics.REVENUE_SERIES], report["series"])


@router.get("/operations.csv", summary="Operations series as CSV (analyst and admin)", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def operations_csv(r: Range = Depends(), _: Principal = Depends(operations_roles), db: Session = Depends(get_tenant_db, scope="function")) -> Response:
    report = _operations(db, Range(r.date_from, r.date_to, r.granularity, False, r.days))
    return _csv(f"operations-{report['range']['from']}-{report['range']['to']}-{r.granularity}.csv", ["period", *analytics.OPERATIONS_SERIES], report["series"])


@router.get("/invoices.csv", summary="Invoices as CSV (admin)", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def invoices_csv(
    invoice_status: InvoiceStatus | None = Query(None, alias="status"), q: str | None = Query(None, max_length=100), date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"), _: Principal = Depends(admin_only), db: Session = Depends(get_tenant_db, scope="function"),
) -> Response:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = analytics.invoice_page(db, invoice_status.value if invoice_status else None, q, date_from, date_to, 200, offset)
        rows += page["items"]
        offset += 200
        if offset >= page["total"]:
            break
    columns = ["id", "customer", "industry", "region", "plan_code", "period_start", "period_end", "status", "total_cents", "issued_at", "due_at", "paid_at"]
    return _csv("invoices.csv", columns, rows)
