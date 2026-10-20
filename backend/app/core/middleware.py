from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# API responses carry no markup, so the policy forbids everything except what the docs UI needs.
CSP_API = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
CSP_DOCS = (
    "default-src 'self'; img-src 'self' data: https://fastapi.tiangolo.com; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; frame-ancestors 'none'"
)
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


class CsrfMiddleware(BaseHTTPMiddleware):
    """Reject state changing requests that lack the custom header.

    Browsers cannot add a custom header to a cross site form post or a simple request, and a
    cross origin script needs a CORS preflight that the origin allowlist refuses. Combined with
    SameSite=Lax cookies this closes cross site request forgery without server side state.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = get_settings()
        if request.method not in SAFE_METHODS:
            if request.headers.get(settings.csrf_header_name) != settings.csrf_header_value:
                return JSONResponse({"detail": "CSRF check failed"}, status_code=403)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        settings = get_settings()
        is_docs = request.url.path.startswith(DOCS_PATHS)
        response.headers["Content-Security-Policy"] = CSP_DOCS if is_docs else CSP_API
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        if settings.hsts_enabled:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response
