"""One error format for the whole API.

Every error body has the original FastAPI `detail` (so clients that read it keep working) and an `error` object with a
stable machine readable `code`, a human `message`, the HTTP status and, for validation errors, the offending fields.
Validation errors never echo the submitted value: it may be a password.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

CODES = {
    400: "bad_request", 401: "not_authenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed", 409: "conflict", 413: "payload_too_large",
    415: "unsupported_media_type", 422: "validation_error", 429: "rate_limited", 500: "internal_error", 502: "upstream_error", 503: "unavailable",
}  # fmt: skip


def error_body(status_code: int, message: str, detail: Any = None, fields: list[dict[str, str]] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": CODES.get(status_code, f"http_{status_code}"), "message": message, "status": status_code}
    if fields:
        error["fields"] = fields
    return {"detail": message if detail is None else detail, "error": error}


def _message(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        return detail["message"]
    return "Request failed"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(error_body(exc.status_code, _message(exc.detail), exc.detail), status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"field": ".".join(str(part) for part in e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        detail = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]  # no `input`, no `ctx`
        return JSONResponse(error_body(422, "The request is not valid", detail, fields), status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(error_body(500, "Internal server error"), status_code=500)
