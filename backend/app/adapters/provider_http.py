"""HTTP adapter for the travel provider contract (assignment/openAPI.json).

Every call is bounded by an overall deadline (``asyncio.timeout``), not only httpx's
per-phase timeouts, so a slow-dripping provider cannot keep a caller waiting.
Every call is logged with the provider's requestId for diagnostics.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx

from app.domain.provider import (
    CATEGORIES,
    BookingRequest,
    IdempotencyConflict,
    Offer,
    OfferChanged,
    OfferUnavailable,
    ProviderAuthFailed,
    ProviderBadRequest,
    ProviderBooking,
    ProviderConflict,
    ProviderError,
    ProviderNotFound,
    ProviderProtocolError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderTransportError,
    ProviderUnavailable,
    ProviderUnreachable,
    ReferenceConflict,
    SearchQuery,
    SearchResult,
    SupplierWarning,
)
from app.observability import request_id_var

logger = logging.getLogger("app.provider")

CONFLICTS: dict[str, type[ProviderConflict]] = {
    "OFFER_CHANGED": OfferChanged,
    "OFFER_UNAVAILABLE": OfferUnavailable,
    "IDEMPOTENCY_CONFLICT": IdempotencyConflict,
    "REFERENCE_CONFLICT": ReferenceConflict,
}


class HttpProviderClient:
    def __init__(
        self, base_url: str, token: str, timeout_seconds: float,
        *, transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- Operations -------------------------------------------------------------

    async def search(self, query: SearchQuery) -> SearchResult:
        body: dict[str, Any] = {
            "category": query.category,
            "destination": query.destination,
            "startsOn": query.starts_on.isoformat(),
            "endsOn": query.ends_on.isoformat(),
            "travelers": query.travelers,
        }
        if query.origin is not None:
            body["origin"] = query.origin
        if query.provider_id is not None:
            body["providerId"] = query.provider_id
        _, data = await self._call("searchOffers", "POST", "/offers/search", json=body,
                                   expected=(200,))
        return _parse(data, _search_result)

    async def get_offer(self, offer_id: str) -> Offer:
        _, data = await self._call("getOffer", "GET", f"/offers/{_segment(offer_id)}",
                                   expected=(200,))
        return _parse(data, _offer)

    async def create_booking(
        self, request: BookingRequest, *, idempotency_key: str
    ) -> ProviderBooking:
        body = {
            "clientReference": request.client_reference,
            "offerId": request.offer_id,
            "expectedOfferRevision": request.expected_revision,
            "expectedPrice": {"amount": f"{request.expected_price:.2f}", "currency": "USD"},
        }
        status, data = await self._call(
            "createBooking", "POST", "/bookings", json=body,
            headers={"Idempotency-Key": idempotency_key}, expected=(200, 201),
        )
        return _parse(data, lambda d: _booking(d, replayed=status == 200))

    async def get_booking(self, booking_id: str) -> ProviderBooking:
        _, data = await self._call("getBooking", "GET", f"/bookings/{_segment(booking_id)}",
                                   expected=(200,))
        return _parse(data, _booking)

    async def get_booking_by_reference(self, client_reference: str) -> ProviderBooking:
        _, data = await self._call(
            "getBookingByReference", "GET",
            f"/bookings/by-reference/{_segment(client_reference)}", expected=(200,),
        )
        return _parse(data, _booking)

    # --- Transport and error mapping ----------------------------------------------

    async def _call(
        self, operation: str, method: str, path: str, *, expected: tuple[int, ...],
        json: Any = None, headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        started = time.monotonic()
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._client.request(method, path, json=json, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise self._fail(operation, started, ProviderUnreachable(
                f"The travel provider could not be reached ({type(exc).__name__}).")) from exc
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise self._fail(operation, started, ProviderTimeout(
                f"The travel provider did not answer within {self._timeout:g} seconds.")) from exc
        except httpx.TransportError as exc:
            raise self._fail(operation, started, ProviderTransportError(
                f"The connection to the travel provider failed ({type(exc).__name__}).")) from exc

        data = _json_or_none(response)
        provider_request_id = _provider_request_id(response, data)
        elapsed_ms = (time.monotonic() - started) * 1000
        if response.status_code in expected:
            if data is None:
                raise self._fail(operation, started, ProviderProtocolError(
                    "The travel provider returned a non-JSON response.",
                    provider_request_id=provider_request_id))
            logger.info(
                "provider %s -> %s in %.0fms provider_request_id=%s request_id=%s",
                operation, response.status_code, elapsed_ms, provider_request_id,
                request_id_var.get(),
            )
            return response.status_code, data
        raise self._fail(operation, started, _error_for(response, data, provider_request_id),
                         status=response.status_code)

    def _fail(self, operation: str, started: float, error: ProviderError,
              status: int | None = None) -> ProviderError:
        logger.warning(
            "provider %s failed: %s status=%s provider_code=%s message=%r in %.0fms "
            "provider_request_id=%s request_id=%s",
            operation, error.code, status, error.provider_code, error.message,
            (time.monotonic() - started) * 1000, error.provider_request_id,
            request_id_var.get(),
        )
        return error


def _error_for(response: httpx.Response, data: Any, request_id: str | None) -> ProviderError:
    status = response.status_code
    is_error_body = isinstance(data, dict) and isinstance(data.get("code"), str)
    code = data["code"] if is_error_body else None
    message = str(data.get("message")) if is_error_body else f"HTTP {status}"
    if not is_error_body:
        # Not the contract's Error schema (e.g. a proxy page): we cannot tell what happened.
        return ProviderProtocolError(
            f"The travel provider answered HTTP {status} without a contract error body.",
            provider_request_id=request_id)
    if status == 400:
        return ProviderBadRequest(message, provider_code=code, provider_request_id=request_id)
    if status == 401:
        return ProviderAuthFailed(
            "The application's provider credentials were rejected.", provider_code=code,
            provider_request_id=request_id)
    if status == 404:
        return ProviderNotFound(message, provider_code=code, provider_request_id=request_id)
    if status == 409 and code in CONFLICTS:
        return CONFLICTS[code](message, provider_code=code, provider_request_id=request_id)
    if status == 429:
        return ProviderRateLimited(
            message, retry_after_seconds=_retry_after(response), provider_request_id=request_id)
    if status == 503:
        warnings = tuple(
            SupplierWarning(w["providerId"], w["code"], w["message"])
            for w in (data.get("details") or {}).get("warnings", [])
            if isinstance(w, dict) and {"providerId", "code", "message"} <= w.keys()
        )
        return ProviderUnavailable(message, warnings=warnings, provider_request_id=request_id)
    return ProviderProtocolError(
        f"Unexpected provider response HTTP {status} {code}: {message}",
        provider_code=code, provider_request_id=request_id)


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _provider_request_id(response: httpx.Response, data: Any) -> str | None:
    if isinstance(data, dict) and isinstance(data.get("requestId"), str):
        return str(data["requestId"])
    header: str | None = response.headers.get("X-Request-Id")
    return header


def _retry_after(response: httpx.Response) -> int | None:
    try:
        return max(1, int(response.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None


def _segment(value: str) -> str:
    return quote(value, safe="")


# --- Parsing (contract schemas -> domain) -------------------------------------------


def _parse[T](data: Any, parser: Callable[[Any], T]) -> T:
    try:
        result: T = parser(data)
        return result
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        logger.warning("provider response does not match the contract: %r request_id=%s",
                       exc, request_id_var.get())
        raise ProviderProtocolError(
            f"The travel provider returned data that does not match its contract ({exc})."
        ) from exc


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without offset: {value}")
    return parsed


def _offer(d: dict[str, Any]) -> Offer:
    if d["category"] not in CATEGORIES:
        raise ValueError(f"unknown category {d['category']!r}")
    if d["price"]["currency"] != "USD":
        raise ValueError("price currency must be USD")
    restrictions = d.get("restrictions", [])
    if not isinstance(restrictions, list) or not isinstance(d["available"], bool):
        raise TypeError("invalid offer fields")
    return Offer(
        id=str(d["id"]), provider_id=str(d["providerId"]), category=d["category"],
        title=str(d["title"]), description=str(d["description"]),
        destination=str(d["destination"]), origin=d.get("origin"),
        starts_at=_aware(d["startsAt"]), ends_at=_aware(d["endsAt"]),
        travelers=int(d["travelers"]), price=Decimal(d["price"]["amount"]),
        currency="USD", revision=str(d["revision"]), available=d["available"],
        valid_until=_aware(d["validUntil"]), restrictions=tuple(str(r) for r in restrictions),
    )


def _search_result(d: dict[str, Any]) -> SearchResult:
    return SearchResult(
        offers=tuple(_offer(o) for o in d["offers"]),
        partial=bool(d["partial"]),
        warnings=tuple(SupplierWarning(w["providerId"], w["code"], w["message"])
                       for w in d["warnings"]),
        searched_at=_aware(d["searchedAt"]),
    )


def _booking(d: dict[str, Any], replayed: bool = False) -> ProviderBooking:
    if d["status"] not in ("pending", "confirmed", "failed"):
        raise ValueError(f"unknown booking status {d['status']!r}")
    failure = d.get("failure")
    return ProviderBooking(
        id=str(d["id"]), client_reference=str(d["clientReference"]), offer=_offer(d["offer"]),
        status=d["status"], created_at=_aware(d["createdAt"]),
        updated_at=_aware(d["updatedAt"]),
        failure={"code": str(failure["code"]), "message": str(failure["message"])}
        if failure else None,
        replayed=replayed,
    )
