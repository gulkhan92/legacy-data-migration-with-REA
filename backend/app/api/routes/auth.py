import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    client_ip,
    get_principal,
    get_tenant_db,
)
from pydantic import BaseModel

from app.api.schemas import LoginRequest, MeResponse
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import Principal, decode_access_token
from app.db.models import Organization, User
from app.db.session import tenant_session
from app.services import auth as auth_service
from app.services.auth import AuthenticationFailed, AuthResult

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

REFRESH_PATH = "/api/v1/auth"
INVALID = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")


def _set_cookies(response: Response, result: AuthResult) -> None:
    settings = get_settings()
    common = {"httponly": True, "secure": settings.cookie_secure, "samesite": "lax"}
    response.set_cookie(
        ACCESS_COOKIE,
        result.access_token,
        max_age=settings.access_token_minutes * 60,
        path="/",
        **common,  # type: ignore[arg-type]
    )
    response.set_cookie(
        REFRESH_COOKIE,
        result.refresh_token,
        max_age=settings.refresh_token_days * 86400,
        path=REFRESH_PATH,
        **common,  # type: ignore[arg-type]
    )


def _clear_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)


def _me(db: Session, principal: Principal) -> MeResponse:
    user = db.get(User, principal.user_id)
    org = db.get(Organization, principal.org_id)
    if user is None or org is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return MeResponse(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        org_id=org.id,
        org_name=org.name,
        role=principal.role,
    )


@router.post("/login", response_model=MeResponse)
@limiter.limit(get_settings().login_rate_limit)
def login(request: Request, response: Response, body: LoginRequest) -> MeResponse:
    try:
        result = auth_service.login(
            body.email, body.password, ip=client_ip(request), org_id=body.org_id
        )
    except AuthenticationFailed:
        raise INVALID from None
    _set_cookies(response, result)
    with tenant_session(result.principal.org_id) as db:
        return _me(db, result.principal)


@router.post("/refresh", response_model=MeResponse)
@limiter.limit("30/minute")
def refresh(request: Request, response: Response) -> MeResponse | JSONResponse:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise INVALID
    try:
        result = auth_service.refresh(token, ip=client_ip(request))
    except AuthenticationFailed:
        failed = JSONResponse({"detail": "Invalid credentials"}, status_code=status.HTTP_401_UNAUTHORIZED)
        _clear_cookies(failed)
        return failed
    _set_cookies(response, result)
    with tenant_session(result.principal.org_id) as db:
        return _me(db, result.principal)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> Response:
    access = request.cookies.get(ACCESS_COOKIE)
    principal = decode_access_token(access) if access else None
    auth_service.logout(request.cookies.get(REFRESH_COOKIE), principal, ip=client_ip(request))
    _clear_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db)) -> MeResponse:
    return _me(db, principal)


class SwitchRequest(BaseModel):
    org_id: uuid.UUID


@router.get("/organizations", summary="The organizations the signed in user belongs to")
def organizations(principal: Principal = Depends(get_principal)) -> list[dict[str, object]]:
    orgs = auth_service.list_organizations(principal.user_id)
    return [{**o, "current": o["org_id"] == str(principal.org_id)} for o in orgs]


@router.post("/switch", response_model=MeResponse, summary="Switch to another organization of the signed in user")
@limiter.limit("30/minute")
def switch(request: Request, response: Response, body: SwitchRequest, principal: Principal = Depends(get_principal)) -> MeResponse:
    try:
        result = auth_service.switch_organization(principal.user_id, body.org_id, request.cookies.get(REFRESH_COOKIE), ip=client_ip(request))
    except AuthenticationFailed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not belong to that organization") from None
    _set_cookies(response, result)
    with tenant_session(result.principal.org_id) as db:
        return _me(db, result.principal)
