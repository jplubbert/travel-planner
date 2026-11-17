"""The contract validator must actually reject non-conforming data; otherwise every
other test in this suite would pass vacuously."""

OFFER = {
    "id": "offer-x",
    "providerId": "supplier-a",
    "category": "activity",
    "title": "t",
    "description": "d",
    "destination": "Lisbon",
    "startsAt": "2030-06-10T10:00:00+01:00",
    "endsAt": "2030-06-10T12:00:00+01:00",
    "travelers": 2,
    "price": {"amount": "10.00", "currency": "USD"},
    "revision": "r1",
    "available": True,
    "validUntil": "2030-06-01T12:15:00Z",
}


def test_valid_offer_passes(contract):
    assert contract.errors("#/components/schemas/Offer", OFFER) == []


def test_missing_required_field_is_rejected(contract):
    bad = {k: v for k, v in OFFER.items() if k != "revision"}
    assert contract.errors("#/components/schemas/Offer", bad)


def test_unknown_property_is_rejected(contract):
    assert contract.errors("#/components/schemas/Offer", {**OFFER, "extra": 1})


def test_money_pattern_is_enforced(contract):
    bad = {**OFFER, "price": {"amount": "10.5", "currency": "USD"}}
    assert contract.errors("#/components/schemas/Offer", bad)


def test_date_time_format_is_enforced(contract):
    # Requires rfc3339-validator; without it jsonschema silently skips date-time checks.
    assert contract.errors("#/components/schemas/Offer", {**OFFER, "validUntil": "tomorrow"})


def test_nested_refs_are_resolved(contract):
    booking = {
        "id": "b",
        "clientReference": "r",
        "offer": {**OFFER, "travelers": 0},
        "status": "confirmed",
        "createdAt": "2030-06-01T12:00:00Z",
        "updatedAt": "2030-06-01T12:00:00Z",
    }
    assert contract.errors("#/components/schemas/Booking", booking)
