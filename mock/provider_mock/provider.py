"""Provider behavior: quotes, multi-supplier search, idempotent bookings and controls."""

import asyncio
import copy
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from .clock import Clock, iso_utc, parse_iso
from .errors import ProviderError, bad_request, not_found
from .fixtures import SUPPLIERS, TEMPLATES, OfferTemplate, Supplier, offer_id, parse_offer_id
from .models import CreateBookingRequest, SearchRequest
from .settings import Settings
from .store import Store

FAILURE_DEFAULT = {
    "code": "SUPPLIER_REJECTED",
    "message": "The supplier rejected the booking after accepting the request.",
}


@dataclass
class Scenario:
    mode: Literal["normal", "unavailable", "timeout", "rate_limited"]
    delaySeconds: float
    retryAfterSeconds: int
    bookingOutcome: Literal["confirmed", "pending", "failed"] | None


class Provider:
    def __init__(self, settings: Settings, clock: Clock, store: Store) -> None:
        self.settings = settings
        self.clock = clock
        self.store = store
        self.scenarios: dict[str, Scenario] = {}
        # Single-process mock: one lock serializes every write to the dataset.
        self._lock = asyncio.Lock()

    # --- Supplier scenarios ---------------------------------------------------

    def _gate(self, provider_id: str) -> float:
        """Apply the supplier's scenario to a direct operation; returns a response delay."""
        scenario = self.scenarios.get(provider_id)
        if scenario is None:
            return 0.0
        if scenario.mode == "unavailable":
            raise ProviderError(
                503, "PROVIDER_UNAVAILABLE", f"{provider_id} is temporarily unavailable."
            )
        if scenario.mode == "rate_limited":
            raise ProviderError(
                429, "RATE_LIMITED", f"{provider_id} rate limit was reached.",
                headers={"Retry-After": str(scenario.retryAfterSeconds)},
            )
        if scenario.mode == "timeout":
            return scenario.delaySeconds
        return 0.0

    def set_scenario(self, supplier_id: str, scenario: Scenario) -> None:
        if supplier_id not in SUPPLIERS:
            raise bad_request(f"Unknown supplier '{supplier_id}'.")
        if scenario.mode == "normal" and scenario.bookingOutcome is None:
            self.scenarios.pop(supplier_id, None)
        else:
            self.scenarios[supplier_id] = scenario

    def describe_scenarios(self) -> dict[str, Any]:
        return {sid: asdict(s) for sid, s in sorted(self.scenarios.items())}

    # --- Quotes ---------------------------------------------------------------

    def _quote(
        self, template: OfferTemplate, travelers: int, *, refresh: bool
    ) -> tuple[dict[str, Any], bool]:
        """Current offer terms; creates the first quote lazily and, if ``refresh``,
        re-quotes an expired one with a new revision."""
        oid = offer_id(template, travelers)
        now = self.clock.now()
        quote = self.store.quotes.get(oid)
        changed = False
        if quote is None:
            quote = {
                "seq": 1,
                "price": template.price_for(travelers),
                "available": template.available,
                "validUntil": self._valid_until(now),
            }
            self.store.quotes[oid] = quote
            changed = True
        elif refresh and now >= parse_iso(quote["validUntil"]):
            quote["seq"] += 1
            quote["validUntil"] = self._valid_until(now)
            changed = True
        return self._offer(template, travelers, quote), changed

    def _valid_until(self, now: datetime) -> str:
        return iso_utc(now + timedelta(seconds=self.settings.quote_ttl_seconds))

    @staticmethod
    def _offer(template: OfferTemplate, travelers: int, quote: dict[str, Any]) -> dict[str, Any]:
        oid = offer_id(template, travelers)
        offer: dict[str, Any] = {
            "id": oid,
            "providerId": template.provider_id,
            "category": template.category,
            "title": template.title,
            "description": template.describe(travelers),
            "destination": template.destination,
            "startsAt": template.starts_at,
            "endsAt": template.ends_at,
            "travelers": travelers,
            "price": {"amount": quote["price"], "currency": "USD"},
            "revision": f"quote-{oid}-v{quote['seq']}",
            "available": quote["available"],
            "validUntil": quote["validUntil"],
            "restrictions": list(template.restrictions),
        }
        if template.origin is not None:
            offer["origin"] = template.origin
        return offer

    async def get_offer(self, raw_offer_id: str) -> dict[str, Any]:
        parsed = parse_offer_id(raw_offer_id)
        if parsed is None:
            raise not_found(f"Offer '{raw_offer_id}' does not exist.")
        template, travelers = parsed
        delay = self._gate(template.provider_id)
        async with self._lock:
            offer, changed = self._quote(template, travelers, refresh=True)
            if changed:
                self.store.save()
        if delay:
            await asyncio.sleep(delay)
        return offer

    async def set_offer_terms(
        self, raw_offer_id: str, price: str | None, available: bool | None
    ) -> dict[str, Any]:
        parsed = parse_offer_id(raw_offer_id)
        if parsed is None:
            raise not_found(f"Offer '{raw_offer_id}' does not exist.")
        template, travelers = parsed
        async with self._lock:
            self._quote(template, travelers, refresh=False)
            quote = self.store.quotes[offer_id(template, travelers)]
            if price is not None:
                quote["price"] = price
            if available is not None:
                quote["available"] = available
            # Any change of terms is a new quote: new revision and validity.
            quote["seq"] += 1
            quote["validUntil"] = self._valid_until(self.clock.now())
            self.store.save()
            return self._offer(template, travelers, quote)

    # --- Search ---------------------------------------------------------------

    async def search(self, req: SearchRequest) -> dict[str, Any]:
        if req.providerId is not None:
            supplier = SUPPLIERS.get(req.providerId)
            if supplier is None:
                raise bad_request(f"Unknown providerId '{req.providerId}'.")
            if req.category not in supplier.categories:
                raise bad_request(f"Supplier '{supplier.id}' does not offer {req.category}.")
            suppliers = [supplier]
        else:
            suppliers = [s for s in SUPPLIERS.values() if req.category in s.categories]

        outcomes = await asyncio.gather(*(self._search_supplier(s, req) for s in suppliers))
        warnings = [o for o in outcomes if isinstance(o, dict)]
        matches = [m for o in outcomes if isinstance(o, list) for m in o]

        if len(warnings) == len(suppliers):
            if len(suppliers) == 1 and warnings[0]["code"] == "RATE_LIMITED":
                retry_after = self.scenarios[suppliers[0].id].retryAfterSeconds
                raise ProviderError(
                    429, "RATE_LIMITED", warnings[0]["message"],
                    headers={"Retry-After": str(retry_after)},
                )
            raise ProviderError(
                503, "PROVIDER_UNAVAILABLE", "No queried supplier completed the search.",
                details={"warnings": warnings},
            )

        async with self._lock:
            offers, any_changed = [], False
            for template, travelers in matches:
                offer, changed = self._quote(template, travelers, refresh=True)
                offers.append(offer)
                any_changed |= changed
            if any_changed:
                self.store.save()
        offers.sort(key=lambda o: (parse_iso(o["startsAt"]), o["id"]))
        return {
            "offers": offers,
            "partial": bool(warnings),
            "warnings": warnings,
            "searchedAt": iso_utc(self.clock.now()),
        }

    async def _search_supplier(
        self, supplier: Supplier, req: SearchRequest
    ) -> list[tuple[OfferTemplate, int]] | dict[str, str]:
        scenario = self.scenarios.get(supplier.id)
        if scenario is not None:
            if scenario.mode == "unavailable":
                return _warning(supplier, "PROVIDER_UNAVAILABLE", "did not complete the search.")
            if scenario.mode == "rate_limited":
                return _warning(supplier, "RATE_LIMITED", "rate limit was reached.")
            if scenario.mode == "timeout":
                await asyncio.sleep(self.settings.supplier_deadline_seconds)
                return _warning(supplier, "PROVIDER_TIMEOUT", "did not respond in time.")
        return [
            (t, req.travelers)
            for t in TEMPLATES
            if t.provider_id == supplier.id and _matches(t, req)
        ]

    # --- Bookings -------------------------------------------------------------

    async def create_booking(
        self, caller: str, key: str | None, req: CreateBookingRequest
    ) -> tuple[int, dict[str, Any]]:
        if key is None or not 1 <= len(key) <= 128:
            raise bad_request("Idempotency-Key header is required (1-128 characters).")
        fingerprint = json.dumps(req.model_dump(), sort_keys=True, separators=(",", ":"))

        async with self._lock:
            existing = self.store.idempotency.get(caller, {}).get(key)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise ProviderError(
                        409, "IDEMPOTENCY_CONFLICT",
                        "This key was already used with a different request body.",
                    )
                booking = self.store.bookings[existing["bookingId"]]["booking"]
                delay = self._gate(booking["offer"]["providerId"])
                status, result = 200, copy.deepcopy(booking)
            else:
                if req.clientReference in self.store.references.get(caller, {}):
                    raise ProviderError(
                        409, "REFERENCE_CONFLICT",
                        "This client reference belongs to an existing booking request.",
                    )
                parsed = parse_offer_id(req.offerId)
                if parsed is None:
                    raise not_found(f"Offer '{req.offerId}' does not exist.")
                template, travelers = parsed
                delay = self._gate(template.provider_id)
                result = self._accept(caller, key, fingerprint, req, template, travelers)
                status = 201

        # A slow supplier answers late, but the booking above is already committed:
        # a client that gives up waiting has an accepted booking it has not seen.
        if delay:
            await asyncio.sleep(delay)
        return status, result

    def _accept(
        self, caller: str, key: str, fingerprint: str, req: CreateBookingRequest,
        template: OfferTemplate, travelers: int,
    ) -> dict[str, Any]:
        offer, changed = self._quote(template, travelers, refresh=False)
        if changed:
            self.store.save()
        now = self.clock.now()
        if not offer["available"]:
            raise ProviderError(409, "OFFER_UNAVAILABLE", "The offer is no longer available.")
        if (
            now >= parse_iso(offer["validUntil"])
            or offer["revision"] != req.expectedOfferRevision
            or Decimal(offer["price"]["amount"]) != Decimal(req.expectedPrice.amount)
        ):
            raise ProviderError(
                409, "OFFER_CHANGED",
                "Price, quote revision, or quote validity changed; review the latest offer.",
            )

        scenario = self.scenarios.get(template.provider_id)
        outcome = (scenario.bookingOutcome if scenario else None) or template.booking_outcome
        booking_id = f"booking-{self.store.next_booking_seq:06d}"
        booking: dict[str, Any] = {
            "id": booking_id,
            "clientReference": req.clientReference,
            "offer": offer,
            "status": outcome,
            "createdAt": iso_utc(now),
            "updatedAt": iso_utc(now),
        }
        if outcome == "failed":
            booking["failure"] = dict(FAILURE_DEFAULT)

        self.store.next_booking_seq += 1
        self.store.bookings[booking_id] = {"caller": caller, "booking": booking}
        self.store.references.setdefault(caller, {})[req.clientReference] = booking_id
        self.store.idempotency.setdefault(caller, {})[key] = {
            "fingerprint": fingerprint,
            "bookingId": booking_id,
        }
        self.store.save()
        return copy.deepcopy(booking)

    async def get_booking(self, caller: str, booking_id: str) -> dict[str, Any]:
        record = self.store.bookings.get(booking_id)
        if record is None or record["caller"] != caller:
            raise not_found(f"Booking '{booking_id}' is not visible to this caller.")
        return await self._read(record["booking"])

    async def get_booking_by_reference(self, caller: str, reference: str) -> dict[str, Any]:
        booking_id = self.store.references.get(caller, {}).get(reference)
        if booking_id is None:
            raise not_found(f"No accepted booking with clientReference '{reference}'.")
        return await self._read(self.store.bookings[booking_id]["booking"])

    async def _read(self, booking: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(booking)
        delay = self._gate(booking["offer"]["providerId"])
        if delay:
            await asyncio.sleep(delay)
        return result

    async def settle(
        self, booking_id: str, status: str, failure: dict[str, str] | None
    ) -> dict[str, Any]:
        async with self._lock:
            record = self.store.bookings.get(booking_id)
            if record is None:
                raise not_found(f"Booking '{booking_id}' does not exist.")
            booking = record["booking"]
            if booking["status"] != "pending":
                raise ProviderError(
                    409, "BOOKING_NOT_PENDING",
                    f"Booking is already {booking['status']}; terminal outcomes do not reverse.",
                )
            booking["status"] = status
            booking["updatedAt"] = iso_utc(max(self.clock.now(), parse_iso(booking["createdAt"])))
            if status == "failed":
                booking["failure"] = failure or dict(FAILURE_DEFAULT)
            self.store.save()
            return copy.deepcopy(booking)

    async def reset(self) -> None:
        async with self._lock:
            self.store.clear()
            self.store.save()
            self.scenarios.clear()
            self.clock.reset()


def _warning(supplier: Supplier, code: str, text: str) -> dict[str, str]:
    return {"providerId": supplier.id, "code": code, "message": f"{supplier.name} {text}"}


def _matches(template: OfferTemplate, req: SearchRequest) -> bool:
    if template.category != req.category:
        return False
    if template.destination.casefold() != req.destination.casefold():
        return False
    if req.category == "transport" and (
        template.origin is None or req.origin is None
        or template.origin.casefold() != req.origin.casefold()
    ):
        return False
    if req.travelers > template.max_travelers:
        return False
    # Both ends must fall inside the inclusive window, using each instant's own local date.
    return req.starts_on <= template.start_date() and template.end_date() <= req.ends_on
