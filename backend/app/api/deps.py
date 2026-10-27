import uuid
from collections.abc import Callable, Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import Principal, decode_access_token
from app.db.models.enums import Role
from app.db.session import make_session_factory, scoped_session

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


def get_principal(request: Request) -> Principal:
    """The authenticated caller, taken from the signed access token cookie."""
    token = request.cookies.get(ACCESS_COOKIE)
    principal = decode_access_token(token) if token else None
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    request.state.org_id = principal.org_id
    return principal


def get_org_id(principal: Principal = Depends(get_principal)) -> uuid.UUID:
    return principal.org_id


def require_role(*allowed: Role) -> Callable[[Principal], Principal]:
    """Guard a route. Admin is always allowed; list the other roles that may use it."""
    permitted = {Role.ADMIN, *allowed}

    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in permitted:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return principal

    return dependency


def get_tenant_db(principal: Principal = Depends(get_principal)) -> Iterator[Session]:
    """A session whose every transaction runs with app.org_id set to the caller's organization.

    Routes ask for it with `Depends(get_tenant_db, scope="function")`: the session is then committed and closed as soon as the route function
    returns, before the response is sent. With the default scope the commit would come after the response had gone out, and a client that reads
    right after a write (a page that reloads its list when a save succeeds) could still see the old data."""
    with scoped_session(
        org_id=principal.org_id, user_id=principal.user_id, factory=make_session_factory()
    ) as session:
        yield session


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
