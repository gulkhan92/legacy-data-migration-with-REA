"""Seed the demo organization and users. Registration is disabled, so this is the only way in.

Run as the table owner:  uv run python -m app.seed
Passwords come from SEED_ADMIN_PASSWORD, SEED_ANALYST_PASSWORD and SEED_VIEWER_PASSWORD. Each must
satisfy the password policy. Use different values for anything other than local use.
"""

import os
import sys

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password, validate_password
from app.db.models import Membership, Organization, User
from app.db.models.enums import Role

DEMO_ORG_SLUG = "demo"
DEMO_USERS = [
    ("admin@legacylift.example", "Demo Admin", Role.ADMIN, "SEED_ADMIN_PASSWORD"),
    ("analyst@legacylift.example", "Demo Analyst", Role.ANALYST, "SEED_ANALYST_PASSWORD"),
    ("viewer@legacylift.example", "Demo Viewer", Role.VIEWER, "SEED_VIEWER_PASSWORD"),
]


def seed(session: Session, passwords: dict[str, str]) -> None:
    org = session.scalar(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    if org is None:
        org = Organization(name="Demo Organization", slug=DEMO_ORG_SLUG, industry="Retail", region="Global")
        session.add(org)
        session.flush()
    for email, full_name, role, env_name in DEMO_USERS:
        password = passwords[env_name]
        problems = validate_password(password, email=email)
        if problems:
            raise SystemExit(f"{env_name} rejected: " + "; ".join(problems))
        user = session.scalar(select(User).where(func.lower(User.email) == email))
        if user is None:
            user = User(email=email, full_name=full_name, password_hash=hash_password(password))
            session.add(user)
            session.flush()
        exists = session.scalar(
            select(Membership).where(Membership.org_id == org.id, Membership.user_id == user.id)
        )
        if exists is None:
            session.add(Membership(org_id=org.id, user_id=user.id, role=role))
    session.commit()


def main() -> None:
    passwords = {}
    for _, _, _, env_name in DEMO_USERS:
        value = os.environ.get(env_name)
        if not value:
            sys.exit(f"Set {env_name} before seeding (12 characters minimum).")
        passwords[env_name] = value
    engine = create_engine(get_settings().database_admin_url)
    with Session(engine) as session:
        seed(session, passwords)
    print("Seeded demo organization and users.")


if __name__ == "__main__":
    main()
