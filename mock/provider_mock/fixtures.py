"""Deterministic base fixtures.

Destinations Lisbon (2030-06-10..14), Madrid (2030-06-14..18) and Paris (2030-06-18..22)
form a possible multi-city trip; London appears only as a transport origin/destination.

Each template is materialized per party size: offer id ``offer-<template>-p<travelers>``.
Its price covers the whole party and the whole segment/stay/activity:
``base + per_traveler * travelers`` rounded half-up to cents.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

Category = Literal["transport", "accommodation", "activity"]
BookingOutcome = Literal["confirmed", "pending", "failed"]


@dataclass(frozen=True)
class Supplier:
    id: str
    name: str
    categories: frozenset[str]


SUPPLIERS: dict[str, Supplier] = {
    s.id: s
    for s in (
        Supplier("supplier-a", "Supplier A", frozenset({"accommodation", "activity"})),
        Supplier("supplier-b", "Supplier B", frozenset({"transport", "accommodation"})),
        Supplier("supplier-c", "Supplier C", frozenset({"transport", "activity"})),
    )
}


@dataclass(frozen=True)
class OfferTemplate:
    id: str
    provider_id: str
    category: Category
    title: str
    description: str  # "{party}" is replaced with "1 traveler" / "N travelers"
    destination: str
    origin: str | None
    starts_at: str
    ends_at: str
    base: str
    per_traveler: str
    max_travelers: int = 20
    available: bool = True
    restrictions: tuple[str, ...] = ()
    booking_outcome: BookingOutcome = "confirmed"

    def price_for(self, travelers: int) -> str:
        total = Decimal(self.base) + Decimal(self.per_traveler) * travelers
        return str(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def describe(self, travelers: int) -> str:
        party = "1 traveler" if travelers == 1 else f"{travelers} travelers"
        return self.description.replace("{party}", party)

    def start_date(self) -> date:
        return datetime.fromisoformat(self.starts_at).date()

    def end_date(self) -> date:
        return datetime.fromisoformat(self.ends_at).date()


def _t(
    id: str, provider: str, origin: str, destination: str, starts: str, ends: str,
    title: str, per_traveler: str, **kw: object,
) -> OfferTemplate:
    return OfferTemplate(
        id=id, provider_id=provider, category="transport", title=title,
        description=f"{title} from {origin} to {destination} for {{party}}.",
        destination=destination, origin=origin, starts_at=starts, ends_at=ends,
        base="0.00", per_traveler=per_traveler, **kw,  # type: ignore[arg-type]
    )


def _a(
    id: str, provider: str, destination: str, starts: str, ends: str, title: str,
    base: str, per_traveler: str, max_travelers: int, **kw: object,
) -> OfferTemplate:
    return OfferTemplate(
        id=id, provider_id=provider, category="accommodation", title=title,
        description=f"{title} for {{party}}; price covers the whole stay.",
        destination=destination, origin=None, starts_at=starts, ends_at=ends,
        base=base, per_traveler=per_traveler, max_travelers=max_travelers, **kw,  # type: ignore[arg-type]
    )


def _x(
    id: str, provider: str, destination: str, starts: str, ends: str, title: str,
    per_traveler: str, **kw: object,
) -> OfferTemplate:
    return OfferTemplate(
        id=id, provider_id=provider, category="activity", title=title,
        description=f"{title} for {{party}}.",
        destination=destination, origin=None, starts_at=starts, ends_at=ends,
        base="0.00", per_traveler=per_traveler, **kw,  # type: ignore[arg-type]
    )


TEMPLATES: tuple[OfferTemplate, ...] = (
    # --- Transport (supplier-b, supplier-c); destination is the arrival city ---
    _t("lis-flight-lon-am", "supplier-b", "London", "Lisbon",
       "2030-06-10T07:10:00+01:00", "2030-06-10T09:45:00+01:00", "Morning flight", "145.50",
       restrictions=("Cabin baggage only",)),
    _t("lis-flight-lon-pm", "supplier-c", "London", "Lisbon",
       "2030-06-10T13:30:00+01:00", "2030-06-10T16:05:00+01:00", "Afternoon flight", "118.00",
       restrictions=("Checked bag not included",)),
    _t("lis-flight-mad", "supplier-c", "Madrid", "Lisbon",
       "2030-06-10T10:00:00+02:00", "2030-06-10T10:25:00+01:00", "Direct flight", "89.90"),
    _t("lis-train-mad", "supplier-b", "Madrid", "Lisbon",
       "2030-06-11T08:00:00+02:00", "2030-06-11T17:30:00+01:00", "Day train", "64.00",
       available=False, restrictions=("Seat reservation required",)),
    _t("mad-train-lis", "supplier-b", "Lisbon", "Madrid",
       "2030-06-14T08:20:00+01:00", "2030-06-14T14:50:00+02:00", "Day train", "72.40"),
    _t("mad-flight-lis", "supplier-c", "Lisbon", "Madrid",
       "2030-06-14T11:15:00+01:00", "2030-06-14T13:40:00+02:00", "Direct flight", "98.00",
       restrictions=("Cabin baggage only",)),
    _t("mad-flight-lon", "supplier-b", "London", "Madrid",
       "2030-06-14T06:50:00+01:00", "2030-06-14T10:15:00+02:00", "Early flight", "132.00"),
    _t("mad-bus-lis", "supplier-c", "Lisbon", "Madrid",
       "2030-06-14T22:00:00+01:00", "2030-06-15T07:30:00+02:00", "Overnight bus", "39.99",
       restrictions=("Overnight journey", "One bag per traveler")),
    _t("par-train-mad", "supplier-b", "Madrid", "Paris",
       "2030-06-18T07:30:00+02:00", "2030-06-18T17:45:00+02:00", "High-speed train", "119.00"),
    _t("par-flight-mad-am", "supplier-c", "Madrid", "Paris",
       "2030-06-18T09:05:00+02:00", "2030-06-18T11:10:00+02:00", "Morning flight", "104.50"),
    _t("par-flight-mad-pm", "supplier-c", "Madrid", "Paris",
       "2030-06-18T18:40:00+02:00", "2030-06-18T20:45:00+02:00", "Evening flight", "87.00",
       restrictions=("Cabin baggage only",)),
    _t("par-flight-lis", "supplier-b", "Lisbon", "Paris",
       "2030-06-18T12:00:00+01:00", "2030-06-18T15:35:00+02:00", "Direct flight", "126.00"),
    _t("lon-train-par", "supplier-b", "Paris", "London",
       "2030-06-22T16:01:00+02:00", "2030-06-22T17:30:00+01:00", "Channel train", "156.00"),
    _t("lon-flight-par", "supplier-c", "Paris", "London",
       "2030-06-22T19:20:00+02:00", "2030-06-22T19:45:00+01:00", "Evening flight", "99.00"),
    # --- Accommodation (supplier-a, supplier-b) ---
    _a("lis-hotel-central", "supplier-a", "Lisbon",
       "2030-06-10T15:00:00+01:00", "2030-06-14T11:00:00+01:00",
       "Four-night central hotel stay", "480.00", "95.00", 4,
       restrictions=("Non-refundable simulated booking",)),
    _a("lis-apartment-alfama", "supplier-b", "Lisbon",
       "2030-06-10T16:00:00+01:00", "2030-06-14T10:00:00+01:00",
       "Four-night Alfama apartment", "520.00", "40.00", 6,
       restrictions=("No check-in after 22:00",)),
    _a("lis-hostel-baixa", "supplier-a", "Lisbon",
       "2030-06-10T14:00:00+01:00", "2030-06-14T11:00:00+01:00",
       "Four-night Baixa hostel beds", "0.00", "112.80", 8,
       restrictions=("Shared bathroom",)),
    _a("lis-hotel-belem", "supplier-b", "Lisbon",
       "2030-06-11T15:00:00+01:00", "2030-06-14T11:00:00+01:00",
       "Three-night Belém boutique hotel", "690.00", "0.00", 2, available=False),
    _a("mad-hotel-sol", "supplier-a", "Madrid",
       "2030-06-14T15:00:00+02:00", "2030-06-18T12:00:00+02:00",
       "Four-night hotel near Sol", "560.00", "80.00", 4),
    _a("mad-apartment-lavapies", "supplier-b", "Madrid",
       "2030-06-14T16:00:00+02:00", "2030-06-18T11:00:00+02:00",
       "Four-night Lavapiés apartment", "430.00", "55.50", 5,
       restrictions=("Cleaning fee included",)),
    _a("mad-hotel-retiro", "supplier-a", "Madrid",
       "2030-06-15T14:00:00+02:00", "2030-06-18T12:00:00+02:00",
       "Three-night hotel by Retiro park", "610.00", "0.00", 2),
    _a("mad-hostel-malasana", "supplier-b", "Madrid",
       "2030-06-14T14:00:00+02:00", "2030-06-18T10:00:00+02:00",
       "Four-night Malasaña hostel beds", "0.00", "98.00", 10,
       restrictions=("Shared bathroom",)),
    _a("par-hotel-marais", "supplier-a", "Paris",
       "2030-06-18T15:00:00+02:00", "2030-06-22T11:00:00+02:00",
       "Four-night Marais hotel stay", "880.00", "120.00", 4,
       restrictions=("Non-refundable simulated booking",)),
    _a("par-apartment-montmartre", "supplier-b", "Paris",
       "2030-06-18T16:00:00+02:00", "2030-06-22T10:00:00+02:00",
       "Four-night Montmartre apartment", "720.00", "60.00", 6,
       booking_outcome="pending",
       restrictions=("Host confirms bookings manually",)),
    _a("par-hotel-latin", "supplier-a", "Paris",
       "2030-06-18T14:00:00+02:00", "2030-06-22T12:00:00+02:00",
       "Four-night Latin Quarter hotel", "650.00", "90.00", 3),
    _a("par-hostel-canal", "supplier-b", "Paris",
       "2030-06-18T15:00:00+02:00", "2030-06-22T11:00:00+02:00",
       "Four-night Canal Saint-Martin hostel beds", "0.00", "135.25", 8),
    # --- Activities (supplier-a, supplier-c); some overlap on purpose ---
    _x("lis-tram-tour", "supplier-a", "Lisbon",
       "2030-06-11T10:00:00+01:00", "2030-06-11T12:00:00+01:00", "Historic tram tour", "28.00"),
    _x("lis-sintra-day", "supplier-c", "Lisbon",
       "2030-06-11T09:00:00+01:00", "2030-06-11T17:30:00+01:00", "Sintra day trip", "74.50",
       restrictions=("Comfortable shoes recommended",)),
    _x("lis-fado-night", "supplier-a", "Lisbon",
       "2030-06-12T20:30:00+01:00", "2030-06-12T23:00:00+01:00", "Fado dinner show", "45.00"),
    _x("lis-surf-lesson", "supplier-c", "Lisbon",
       "2030-06-13T09:30:00+01:00", "2030-06-13T12:30:00+01:00", "Beginner surf lesson",
       "59.99", booking_outcome="failed", restrictions=("Minimum age 12",)),
    _x("lis-food-walk", "supplier-a", "Lisbon",
       "2030-06-13T11:00:00+01:00", "2030-06-13T14:00:00+01:00", "Food walking tour", "52.00"),
    _x("mad-prado-tour", "supplier-a", "Madrid",
       "2030-06-15T10:00:00+02:00", "2030-06-15T12:30:00+02:00", "Prado museum guided tour",
       "39.00"),
    _x("mad-toledo-day", "supplier-c", "Madrid",
       "2030-06-15T08:30:00+02:00", "2030-06-15T18:00:00+02:00", "Toledo day trip", "82.00"),
    _x("mad-flamenco", "supplier-c", "Madrid",
       "2030-06-16T21:00:00+02:00", "2030-06-16T22:30:00+02:00", "Flamenco show", "48.00"),
    _x("mad-tapas-walk", "supplier-a", "Madrid",
       "2030-06-16T19:00:00+02:00", "2030-06-16T22:00:00+02:00", "Tapas walking tour", "55.00"),
    _x("par-louvre-tour", "supplier-a", "Paris",
       "2030-06-19T09:30:00+02:00", "2030-06-19T12:30:00+02:00", "Louvre guided tour", "65.00"),
    _x("par-seine-cruise", "supplier-c", "Paris",
       "2030-06-19T11:00:00+02:00", "2030-06-19T12:15:00+02:00", "Seine river cruise", "22.50"),
    _x("par-versailles-day", "supplier-c", "Paris",
       "2030-06-20T08:00:00+02:00", "2030-06-20T17:00:00+02:00", "Versailles day trip", "96.00"),
    _x("par-cooking-class", "supplier-a", "Paris",
       "2030-06-21T10:00:00+02:00", "2030-06-21T13:30:00+02:00", "French cooking class",
       "120.00", available=False, restrictions=("Maximum 8 participants",)),
)

TEMPLATES_BY_ID: dict[str, OfferTemplate] = {t.id: t for t in TEMPLATES}

_OFFER_ID = re.compile(r"^offer-(?P<template>[a-z0-9-]+)-p(?P<travelers>[1-9][0-9]?)$")


def offer_id(template: OfferTemplate, travelers: int) -> str:
    return f"offer-{template.id}-p{travelers}"


def parse_offer_id(value: str) -> tuple[OfferTemplate, int] | None:
    match = _OFFER_ID.match(value)
    if match is None:
        return None
    template = TEMPLATES_BY_ID.get(match["template"])
    travelers = int(match["travelers"])
    if template is None or not 1 <= travelers <= template.max_travelers:
        return None
    return template, travelers
