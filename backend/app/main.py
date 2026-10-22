from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.routes import admin, admin_llm, auth, discovery, mapping, profiling, sources, transform
from app.core.config import get_settings
from app.core.middleware import CsrfMiddleware, SecurityHeadersMiddleware
from app.core.rate_limit import limiter


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LegacyLift API", version="0.1.0")

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

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
