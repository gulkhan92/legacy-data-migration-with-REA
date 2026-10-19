import uuid

import pytest
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.db import models
from app.db.session import tenant_session
from tests.conftest import Tenant

# (model, name) of every strict tenant table seeded with one row per organization.
SEEDED_MODELS = [
    models.Membership,
    models.Source,
    models.Job,
    models.SourceColumn,
    models.MappingMemory,
    models.LlmCache,
    models.LlmCall,
    models.Customer,
    models.QuarantinedRow,
]


@pytest.mark.parametrize("model", SEEDED_MODELS, ids=lambda m: m.__tablename__)
def test_org_a_cannot_read_org_b_rows(app_factory, tenants: tuple[Tenant, Tenant], model) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with tenant_session(a.org_id, app_factory) as session:
        org_ids = set(session.scalars(select(model.org_id)))
    assert org_ids == {a.org_id}
    with tenant_session(b.org_id, app_factory) as session:
        org_ids = set(session.scalars(select(model.org_id)))
    assert org_ids == {b.org_id}


def test_direct_lookup_of_another_orgs_row_returns_nothing(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with tenant_session(a.org_id, app_factory) as session:
        assert session.get(models.Job, b.job_id) is None
        assert session.get(models.Customer, b.customer_id) is None
        assert session.get(models.Job, a.job_id) is not None


def test_organizations_table_exposes_only_own_row(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    with tenant_session(a.org_id, app_factory) as session:
        ids = set(session.scalars(select(models.Organization.id)))
    assert ids == {a.org_id}


def test_no_tenant_means_no_rows(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    with app_factory() as session:  # plain session, app.org_id never set
        assert session.scalars(select(models.Job)).all() == []
        assert session.scalars(select(models.Organization)).all() == []
        assert session.scalars(select(models.Customer)).all() == []


def test_org_a_cannot_write_rows_for_org_b(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with pytest.raises(DBAPIError), tenant_session(a.org_id, app_factory) as session:
        session.add(models.Source(org_id=b.org_id, name="intruder", kind="csv"))
        session.flush()


def test_org_a_cannot_move_its_row_to_org_b(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with pytest.raises(DBAPIError), tenant_session(a.org_id, app_factory) as session:
        session.execute(update(models.Source).where(models.Source.id == a.source_id).values(org_id=b.org_id))


def test_org_a_cannot_update_or_delete_org_b_rows(app_factory, tenants, admin_engine) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with tenant_session(a.org_id, app_factory) as session:
        updated = session.execute(
            update(models.Source).where(models.Source.id == b.source_id).values(name="hacked")
        )
        deleted = session.execute(delete(models.QuarantinedRow).where(models.QuarantinedRow.org_id == b.org_id))
        assert updated.rowcount == 0
        assert deleted.rowcount == 0
    with Session(admin_engine) as owner:
        assert owner.get(models.Source, b.source_id).name == "bravo source"
        assert owner.query(models.QuarantinedRow).filter_by(org_id=b.org_id).count() == 1


def test_tenant_setting_does_not_leak_between_pooled_connections(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    with tenant_session(a.org_id, app_factory) as session:
        assert session.scalar(select(text("current_setting('app.org_id', true)"))) == str(a.org_id)
    # The pool hands the same connection to a session that never set a tenant.
    with app_factory() as session:
        assert session.scalar(select(text("coalesce(current_setting('app.org_id', true), '')"))) == ""
        assert session.scalars(select(models.Job)).all() == []


def test_tenant_survives_commit_inside_a_session(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with tenant_session(a.org_id, app_factory) as session:
        session.execute(
            insert(models.Source).values(org_id=a.org_id, name="before-commit", kind="csv")
        )
        session.commit()
        names = set(session.scalars(select(models.Source.name)))
        assert "before-commit" in names
        assert "bravo source" not in names


def test_shared_target_schemas_are_readable_but_not_writable(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with tenant_session(a.org_id, app_factory) as session:
        names = set(session.scalars(select(models.TargetSchema.name)))
        assert "builtin-shared" in names
        assert "alpha schema" in names
        assert "bravo schema" not in names
        deleted = session.execute(delete(models.TargetSchema).where(models.TargetSchema.name == "builtin-shared"))
        assert deleted.rowcount == 0
    with pytest.raises(DBAPIError), tenant_session(a.org_id, app_factory) as session:
        session.add(models.TargetSchema(org_id=None, name="forged-builtin"))
        session.flush()


def test_target_fields_follow_the_same_rule(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with tenant_session(a.org_id, app_factory) as session:
        assert session.get(models.TargetField, a.field_id) is not None
        assert session.get(models.TargetField, b.field_id) is None


def test_audit_log_is_append_only_and_scoped(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with tenant_session(a.org_id, app_factory) as session:
        # Before an organization is known. Core insert without RETURNING: the new row is not
        # visible to any tenant, so reading it back in the same statement would be refused.
        session.execute(
            text("INSERT INTO audit_log (org_id, action) VALUES (NULL, 'login_failed')")
        )
        session.add(models.AuditLog(org_id=a.org_id, action="upload"))
        session.flush()
        visible = set(session.scalars(select(models.AuditLog.org_id)))
        assert visible == {a.org_id}
    with pytest.raises(DBAPIError), tenant_session(a.org_id, app_factory) as session:
        session.add(models.AuditLog(org_id=b.org_id, action="forged"))
        session.flush()
    with pytest.raises(ProgrammingError), tenant_session(a.org_id, app_factory) as session:
        session.execute(update(models.AuditLog).values(action="tampered"))
    with pytest.raises(ProgrammingError), tenant_session(a.org_id, app_factory) as session:
        session.execute(delete(models.AuditLog))


def test_application_role_cannot_create_organizations_or_edit_plans(app_factory) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ProgrammingError), tenant_session(uuid.uuid4(), app_factory) as session:
        session.execute(
            insert(models.Organization).values(name="rogue", slug=f"rogue-{uuid.uuid4().hex[:6]}")
        )
    with pytest.raises(ProgrammingError), tenant_session(uuid.uuid4(), app_factory) as session:
        session.execute(
            insert(models.Plan).values(
                code="free-for-all", name="x", monthly_fee_cents=0, included_records=1, overage_cents_per_1000=0
            )
        )


def test_warehouse_rows_are_isolated(app_factory: sessionmaker[Session], tenants) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with tenant_session(a.org_id, app_factory) as session:
        rows = session.scalars(select(models.Customer)).all()
        assert [r.org_id for r in rows] == [a.org_id]
