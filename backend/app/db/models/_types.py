from enum import Enum as PyEnum

from sqlalchemy import Enum


def str_enum(enum_cls: type[PyEnum], name: str) -> Enum:
    """Store an enum as text guarded by a CHECK constraint (simple to migrate)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )
