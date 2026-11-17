"""One error shape for every failure: {code, message, details?, requestId}."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import (
    Conflict,
    DomainError,
    Forbidden,
    InvalidTransition,
    NotFound,
    ValidationFailed,
    VersionConflict,
    VersionRequired,
)
from app.domain.provider import (
    ProviderConflict,
    ProviderError,
    ProviderNotFound,
    ProviderTimeout,
)


class Unauthenticated(Exception):
    pass


def error_response(
    request: Request, status: int, code: str, message: str, details: Any = None
) -> JSONResponse:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "requestId": getattr(request.state, "request_id", None),
    }
    if details is not None:
        body["details"] = details
    return JSONResponse(body, status_code=status)


def _field_name(loc: tuple[Any, ...]) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    name = ""
    for part in parts:
        name += f"[{part}]" if part.isdigit() else (f".{part}" if name else part)
    return name or "body"


def install_error_handlers(app: FastAPI, conflict_details: Any) -> None:
    @app.exception_handler(Unauthenticated)
    async def unauthenticated(request: Request, exc: Unauthenticated) -> JSONResponse:
        return error_response(request, 401, "UNAUTHENTICATED", str(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": _field_name(tuple(e.get("loc", ()))), "message": str(e.get("msg", ""))}
            for e in exc.errors()
        ]
        return error_response(request, 422, "VALIDATION_ERROR",
                              "Some fields are invalid; see details.", {"fields": fields})

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        if isinstance(exc, ValidationFailed):
            fields = [{"field": e.field, "message": e.message} for e in exc.errors]
            return error_response(request, 422, exc.code, exc.message, {"fields": fields})
        if isinstance(exc, VersionConflict):
            return error_response(request, 409, exc.code, exc.message, conflict_details(exc))
        if isinstance(exc, ProviderError):
            status = 504 if isinstance(exc, ProviderTimeout) else (
                409 if isinstance(exc, ProviderConflict) else
                404 if isinstance(exc, ProviderNotFound) else 503)
            return error_response(request, status, exc.code, exc.message,
                                  {"providerRequestId": exc.provider_request_id})
        status = {
            NotFound: 404, Forbidden: 403, Conflict: 409, VersionRequired: 400,
            InvalidTransition: 409,
        }.get(type(exc), 400)
        return error_response(request, status, exc.code, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return error_response(request, exc.status_code, code, str(exc.detail))
