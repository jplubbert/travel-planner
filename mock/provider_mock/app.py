"""FastAPI application: public contract under /v1, test controls under /_control."""

import hashlib
import logging
import secrets
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from .clock import Clock, iso_utc
from .errors import ProviderError, bad_request
from .models import (
    ClockRequest,
    CreateBookingRequest,
    OfferTermsRequest,
    ScenarioRequest,
    SearchRequest,
    SettleRequest,
)
from .provider import Provider, Scenario
from .settings import Settings
from .store import Store

logger = logging.getLogger("provider_mock")


def _error_body(request: Request, code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "requestId": getattr(request.state, "request_id", "request-unknown"),
    }
    if details is not None:
        body["details"] = details
    return body


def _caller_for(authorization: str | None, tokens: tuple[str, ...]) -> str | None:
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    for candidate in tokens:
        if secrets.compare_digest(token.encode(), candidate.encode()):
            # Store a stable caller id, never the token itself.
            return "caller-" + hashlib.sha256(token.encode()).hexdigest()[:12]
    return None


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    clock = clock or Clock()
    provider = Provider(settings, clock, Store(settings.state_file))
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    app = FastAPI(title="Travel Provider Mock", version="0.1.0", docs_url="/_docs")
    app.state.provider = provider

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Response:
        request.state.request_id = f"req-{uuid.uuid4().hex[:16]}"
        caller = None
        if request.url.path == "/v1" or request.url.path.startswith("/v1/"):
            caller = _caller_for(request.headers.get("authorization"), settings.tokens)
            if caller is None:
                response: Response = JSONResponse(
                    _error_body(
                        request, "UNAUTHORIZED", "The provider token is missing or invalid."
                    ),
                    status_code=401,
                )
            else:
                request.state.caller = caller
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        logger.info(
            "%s %s -> %s request_id=%s caller=%s",
            request.method, request.url.path, response.status_code,
            request.state.request_id, caller or "-",
        )
        return response

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, exc: ProviderError) -> JSONResponse:
        return JSONResponse(
            _error_body(request, exc.code, exc.message, exc.details),
            status_code=exc.status,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"location": [str(p) for p in e.get("loc", ())], "message": str(e.get("msg", ""))}
            for e in exc.errors()
        ]
        return JSONResponse(
            _error_body(request, "BAD_REQUEST", "Invalid request parameters or body.",
                        {"errors": errors}),
            status_code=400,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "BAD_REQUEST"
        return JSONResponse(
            _error_body(request, code, str(exc.detail)), status_code=exc.status_code
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error request_id=%s", request.state.request_id)
        return JSONResponse(
            _error_body(request, "PROVIDER_UNAVAILABLE",
                        "The requested provider operation is temporarily unavailable."),
            status_code=503,
        )

    app.include_router(_public_router(provider), prefix="/v1")
    app.include_router(_control_router(provider, clock), prefix="/_control")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _public_router(provider: Provider) -> APIRouter:
    router = APIRouter()

    @router.post("/offers/search")
    async def search_offers(body: SearchRequest) -> dict[str, Any]:
        return await provider.search(body)

    @router.get("/offers/{offer_id}")
    async def get_offer(offer_id: str) -> dict[str, Any]:
        return await provider.get_offer(offer_id)

    @router.post("/bookings")
    async def create_booking(
        request: Request,
        body: CreateBookingRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        status, booking = await provider.create_booking(
            request.state.caller, idempotency_key, body
        )
        return JSONResponse(booking, status_code=status)

    @router.get("/bookings/by-reference/{client_reference}")
    async def get_booking_by_reference(request: Request, client_reference: str) -> dict[str, Any]:
        return await provider.get_booking_by_reference(request.state.caller, client_reference)

    @router.get("/bookings/{booking_id}")
    async def get_booking(request: Request, booking_id: str) -> dict[str, Any]:
        return await provider.get_booking(request.state.caller, booking_id)

    return router


def _control_router(provider: Provider, clock: Clock) -> APIRouter:
    """Test and demo controls. Not part of the provider contract; the application
    must never call these."""
    router = APIRouter()

    def clock_state() -> dict[str, Any]:
        return {"now": iso_utc(clock.now()), "frozen": clock.frozen}

    @router.get("/scenario")
    async def list_scenarios() -> dict[str, Any]:
        return {"scenarios": provider.describe_scenarios()}

    @router.post("/scenario")
    async def set_scenario(body: ScenarioRequest) -> dict[str, Any]:
        provider.set_scenario(
            body.supplierId,
            Scenario(body.mode, body.delaySeconds, body.retryAfterSeconds, body.bookingOutcome),
        )
        return {"scenarios": provider.describe_scenarios()}

    @router.delete("/scenario")
    async def clear_scenarios() -> dict[str, Any]:
        provider.scenarios.clear()
        return {"scenarios": {}}

    @router.get("/clock")
    async def get_clock() -> dict[str, Any]:
        return clock_state()

    @router.post("/clock")
    async def set_clock(body: ClockRequest) -> dict[str, Any]:
        if body.now is None and body.advanceSeconds is None:
            raise bad_request("Provide 'now' and/or 'advanceSeconds'.")
        if body.now is not None:
            if body.now.tzinfo is None:
                raise bad_request("'now' must include a timezone offset.")
            clock.set(body.now, frozen=body.frozen)
        if body.advanceSeconds is not None:
            clock.advance(body.advanceSeconds)
        return clock_state()

    @router.delete("/clock")
    async def reset_clock() -> dict[str, Any]:
        clock.reset()
        return clock_state()

    @router.post("/reset")
    async def reset() -> dict[str, Any]:
        await provider.reset()
        return {"reset": True, "clock": clock_state()}

    @router.post("/booking/{booking_id}/settle")
    async def settle(booking_id: str, body: SettleRequest) -> dict[str, Any]:
        failure = body.failure.model_dump() if body.failure else None
        return await provider.settle(booking_id, body.status, failure)

    @router.post("/offers/{offer_id}")
    async def set_offer_terms(offer_id: str, body: OfferTermsRequest) -> dict[str, Any]:
        if body.price is None and body.available is None:
            raise bad_request("Provide 'price' and/or 'available'.")
        return await provider.set_offer_terms(offer_id, body.price, body.available)

    return router
