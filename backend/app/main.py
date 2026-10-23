from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.errors import install_error_handlers
from app.api.routes import admin, admin_llm, analytics, auth, billing, discovery, jobs, mapping, profiling, public, sources, transform
from app.core.config import get_settings
from app.core.middleware import CsrfMiddleware, SecurityHeadersMiddleware
from app.core.rate_limit import limiter


DESCRIPTION = """Migrate legacy systems into a clean target schema: upload a source, let the platform discover, profile and map it,
review the rules, load it with reconciliation, and roll it back by job id.

Authentication is by HTTP-only cookies (`POST /api/v1/auth/login`). Every request that changes data must send the header
`X-Requested-With: LegacyLift`. Errors have the shape `{"detail": ..., "error": {"code", "message", "status", "fields"?}}`."""

TAGS = [
    {"name": "auth", "description": "Sign in, refresh, sign out, who am I"},
    {"name": "sources", "description": "Uploaded legacy systems and the bundled sample systems"},
    {"name": "jobs", "description": "A migration of one source: create, run stages in the background, follow progress, cancel"},
    {"name": "discovery", "description": "What the system is: structure recovered by static analysis, with evidence"},
    {"name": "profiling", "description": "Column profiles, PII and data quality"},
    {"name": "mapping", "description": "Target schemas and the mapping of source columns to target fields"},
    {"name": "transform", "description": "Mapping review, transform rules, dry run, load, reconciliation, quarantine, rollback"},
    {"name": "billing", "description": "The organization's plan, usage meter, invoices (admin for invoices and plan changes)"},
    {"name": "analytics", "description": "Revenue (admin) and operations (analyst and admin) metrics over daily snapshots, with comparison, granularity and CSV export"},
    {"name": "admin", "description": "Users, roles, audit log and leads (admin only)"},
    {"name": "public", "description": "Endpoints for the marketing site, no sign in"},
    {"name": "ops", "description": "Health"},
]


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LegacyLift API", version="0.1.0", description=DESCRIPTION, openapi_tags=TAGS)
    install_error_handlers(app)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # The last middleware added is the outermost one: CORS answers first, then security headers
    # wrap every response (including CSRF and rate limit rejections).
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", settings.csrf_header_name],
    )

    app.include_router(auth.router)
    app.include_router(sources.router)
    app.include_router(admin.router)
    app.include_router(admin_llm.router)
    app.include_router(discovery.router)
    app.include_router(profiling.router)
    app.include_router(mapping.router)
    app.include_router(transform.router)
    app.include_router(jobs.router)
    app.include_router(public.router)
    app.include_router(billing.router)
    app.include_router(analytics.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
