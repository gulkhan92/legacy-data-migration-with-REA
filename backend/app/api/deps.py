import uuid
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.session import make_session_factory, tenant_session


def get_org_id(request: Request) -> uuid.UUID:
    """The organization of the authenticated caller.

    The authentication layer (Phase 2) sets `request.state.org_id` from the verified access
    token. Until a caller is authenticated no organization is known and the request is refused.
    """
    org_id = getattr(request.state, "org_id", None)
    if not isinstance(org_id, uuid.UUID):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return org_id


def get_tenant_db(org_id: uuid.UUID = Depends(get_org_id)) -> Iterator[Session]:
    """A session whose every transaction runs with app.org_id set to the caller's organization."""
    with tenant_session(org_id, make_session_factory()) as session:
        yield session
