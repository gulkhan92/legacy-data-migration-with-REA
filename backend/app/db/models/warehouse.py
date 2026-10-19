"""Migrated data in the `warehouse` schema.

Every row carries org_id (tenant isolation) and job_id (idempotency and rollback by job).
The column sets are the initial canonical targets and are refined together with the target
schema files in the dataset phase.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.db.base import WAREHOUSE_SCHEMA, Base, CreatedAtMixin, IdMixin, TenantMixin


class WarehouseRow(IdMixin, CreatedAtMixin, TenantMixin):
    __abstract__ = True

    @declared_attr
    def job_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, index=True
        )

    source_key: Mapped[str | None] = mapped_column(String(100))


def _wh_fk(table: str) -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{WAREHOUSE_SCHEMA}.{table}.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


# Commerce target


class Customer(WarehouseRow, Base):
    __tablename__ = "customers"
    __table_args__ = {"schema": WAREHOUSE_SCHEMA}

    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    company: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    address_line1: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(100))


class Product(WarehouseRow, Base):
    __tablename__ = "products"
    __table_args__ = {"schema": WAREHOUSE_SCHEMA}

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))


class Order(WarehouseRow, Base):
    __tablename__ = "orders"
    __table_args__ = {"schema": WAREHOUSE_SCHEMA}

    customer_id: Mapped[uuid.UUID] = _wh_fk("customers")
    order_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(32))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    billing_city: Mapped[str | None] = mapped_column(String(100))
    billing_country: Mapped[str | None] = mapped_column(String(100))


class OrderItem(WarehouseRow, Base):
    __tablename__ = "order_items"
    __table_args__ = {"schema": WAREHOUSE_SCHEMA}

    order_id: Mapped[uuid.UUID] = _wh_fk("orders")
    product_id: Mapped[uuid.UUID] = _wh_fk("products")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))


# Clinic target


class Patient(WarehouseRow, Base):
    __tablename__ = "patients"
    __table_args__ = {"schema": WAREHOUSE_SCHEMA}

    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    birth_date: Mapped[date | None] = mapped_column(Date)
    death_date: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(16))
    ssn: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    is_deceased: Mapped[bool | None] = mapped_column(Boolean)


class Encounter(WarehouseRow, Base):
    __tablename__ = "encounters"
    __table_args__ = {"schema": WAREHOUSE_SCHEMA}

    patient_id: Mapped[uuid.UUID] = _wh_fk("patients")
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encounter_class: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    total_cost_cents: Mapped[int | None] = mapped_column(BigInteger)


class Condition(WarehouseRow, Base):
    __tablename__ = "conditions"
    __table_args__ = {"schema": WAREHOUSE_SCHEMA}

    patient_id: Mapped[uuid.UUID] = _wh_fk("patients")
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{WAREHOUSE_SCHEMA}.encounters.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    stop_date: Mapped[date | None] = mapped_column(Date)


class Medication(WarehouseRow, Base):
    __tablename__ = "medications"
    __table_args__ = {"schema": WAREHOUSE_SCHEMA}

    patient_id: Mapped[uuid.UUID] = _wh_fk("patients")
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{WAREHOUSE_SCHEMA}.encounters.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    stop_date: Mapped[date | None] = mapped_column(Date)
    total_cost_cents: Mapped[int | None] = mapped_column(BigInteger)


class Observation(WarehouseRow, Base):
    __tablename__ = "observations"
    __table_args__ = (
        Index("ix_observations_patient_id_code", "patient_id", "code"),
        {"schema": WAREHOUSE_SCHEMA},
    )

    patient_id: Mapped[uuid.UUID] = _wh_fk("patients")
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{WAREHOUSE_SCHEMA}.encounters.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(String(100))
    units: Mapped[str | None] = mapped_column(String(50))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
