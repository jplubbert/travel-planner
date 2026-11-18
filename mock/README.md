# mock — Simulated external provider

Standalone FastAPI service that implements [`assignment/openAPI.json`](../assignment/openAPI.json). The application consumes it **only over HTTP under `/v1`**. The `/_control/*` endpoints are outside the contract and exist only for tests and the demo; the application must never call them. Design and alternatives: [ADR 0002](../docs/adr/0002-provider-mock-with-control.md).

## How to run it

```bash
# With Docker (recommended): port 4010, data in the "mockdata" volume
docker compose up -d --build provider-mock

# Locally, from the repo root
uv sync
cd mock && uv run python -m provider_mock          # or: uv run uvicorn provider_mock.app:create_app --factory --port 4010
```

- Contract base URL: `http://localhost:4010/v1`. Healthcheck: `GET /healthz`. Mock Swagger: `/_docs`.
- Authentication: `Authorization: Bearer travel-demo-token`. Each token is a distinct *caller*, and bookings, references and idempotency are isolated per caller.
- Logs: one line per request with `request_id` (the same one returned in `X-Request-Id` and in the `requestId` field of errors) and the caller.

| Variable | Default | Use |
| --- | --- | --- |
| `MOCK_TOKENS` | `travel-demo-token` | Accepted tokens, comma-separated |
| `MOCK_STATE_FILE` | `data/mock-state.json` (`/data/...` in Docker) | Persistence file |
| `MOCK_QUOTE_TTL_SECONDS` | `900` | Validity of each quote (`validUntil = now + TTL`) |
| `MOCK_SUPPLIER_DEADLINE_SECONDS` | `2` | How long an aggregated search waits for a supplier in `timeout` |
| `MOCK_HOST` / `MOCK_PORT` | `0.0.0.0` / `4010` | Only for `python -m provider_mock` |

## Tests

```bash
uv run pytest mock/tests
```

Each response is validated against the contract schema for its operation and status code. It uses `jsonschema` (Draft 2020-12) with `$ref` resolved against `openAPI.json` itself and with format checking (`date`, `date-time`). It also checks that the status code is declared for the operation and that 429s carry `Retry-After`. `test_contract_harness.py` verifies that the validator rejects invalid data, so the other tests cannot pass without checking anything.

Coverage: authentication on the 5 operations, request validation, complete/partial/empty/failed search, refresh of expired quotes, `OFFER_CHANGED` (revision, price, expiry), `OFFER_UNAVAILABLE`, idempotency (replay, property order, `IDEMPOTENCY_CONFLICT`), `REFERENCE_CONFLICT`, per-caller isolation, concurrent requests, pending → confirmed/failed, persistence across restarts and lost response.

## Fixtures

Three logical suppliers. Each category is offered by two of them, so partial results can always be demonstrated:

| Supplier | Categories |
| --- | --- |
| `supplier-a` | accommodation, activity |
| `supplier-b` | transport, accommodation |
| `supplier-c` | transport, activity |

The destinations form a possible chained trip in June 2030. Times carry the destination's local offset:

| Destination | Fixture window | Offset |
| --- | --- | --- |
| Lisbon | 2030-06-10 … 2030-06-14 | +01:00 |
| Madrid | 2030-06-14 … 2030-06-18 | +02:00 |
| Paris | 2030-06-18 … 2030-06-22 | +02:00 |

- **Transport** (14 offers): London→Lisbon, Madrid→Lisbon, Lisbon→Madrid, London→Madrid, Madrid→Paris, Lisbon→Paris, Paris→London. `destination` is the arrival city and the search requires `origin`.
- **Accommodation** (12 offers): 4 per destination, each with a maximum capacity (`max_travelers`).
- **Activities** (13 offers): 4 to 5 per destination. Some overlap on purpose to show schedule conflicts, for example `lis-tram-tour` with `lis-sintra-day` on 11 June.
- The full list is in [`provider_mock/fixtures.py`](provider_mock/fixtures.py).

**Identifier and price.** Each fixture is materialized according to the group size: `offer-<fixture>-p<travelers>`, for example `offer-lis-hotel-central-p2`. The price covers the whole group and the whole leg, stay or activity: `base + per_traveler × travelers`, rounded *half-up* to cents. For example, `lis-hotel-central` costs 480.00 + 95.00 × 2 = **670.00 USD** for 2 travelers.

**Fixtures with special behaviour** (nothing needs to be enabled):

| Offer | Behaviour |
| --- | --- |
| `lis-hotel-belem`, `lis-train-mad`, `par-cooking-class` | `available: false`, so booking them gives `409 OFFER_UNAVAILABLE` |
| `par-apartment-montmartre` | The booking stays `pending` until it is resolved with `/settle` |
| `lis-surf-lesson` | The booking is accepted as `failed`, with `failure` |

## Search rules

- `destination` and `origin` are compared case-insensitively. `origin` is ignored outside `transport`.
- An offer matches if its local start date and its local end date fall within `[startsOn, endsOn]`, both inclusive, and if the group fits (`travelers ≤ max_travelers`).
- With no matches it returns `200` with `partial=false` and `offers: []`. An unknown destination also gives an empty result, not an error.
- It returns `400` if `providerId` does not exist or if it exists but does not offer that category.
- Unavailable offers **do** appear, with `available: false`.
- If one supplier fails and another responds: `200`, `partial=true` and one warning per failed supplier. If all queried suppliers fail: `503`, with the warnings in `details`. The exception is a query to a single rate-limited supplier, which returns `429` with `Retry-After`.
- An expired quote (when `now ≥ validUntil`) is renewed with a new `revision` and a new `validUntil` in the search and in `GET /offers/{id}`. While it is still valid, the revision does not change.

## Booking rules

Order of checks in `POST /bookings`:

1. Validation of the body and of the `Idempotency-Key` header (1-128 characters); on failure, `400`.
2. **Key resolution** for that caller. With the same body, it responds `200` with the current state of the booking, even if the quote has expired or changed. With a different body, `409 IDEMPOTENCY_CONFLICT`. The comparison is semantic: the order of JSON properties does not matter.
3. `clientReference` already used by another accepted booking of the same caller: `409 REFERENCE_CONFLICT`.
4. Nonexistent offer: `404`.
5. Supplier scenario: `503` or `429` without creating anything.
6. `available=false`: `409 OFFER_UNAVAILABLE`. This check comes before the terms check.
7. Expired quote, different revision or different price: `409 OFFER_CHANGED`.
8. Acceptance: `201`. The outcome is `confirmed`, `pending` or `failed`, depending on the fixture or the scenario. It is persisted **before** responding.

A rejected request consumes neither the key nor the reference. The booking keeps an immutable copy of the accepted offer and price. Identical concurrent requests create exactly one booking.

## Control scenarios (`/_control`, outside the contract)

No authentication, JSON body. All examples use `B=http://localhost:4010`.

### `POST /_control/scenario`: per-supplier failures

```bash
curl -X POST $B/_control/scenario -H 'Content-Type: application/json' \
  -d '{"supplierId": "supplier-b", "mode": "unavailable"}'
```

| Field | Values | Default |
| --- | --- | --- |
| `supplierId` | `supplier-a` / `supplier-b` / `supplier-c` | (required) |
| `mode` | `normal`, `unavailable`, `timeout`, `rate_limited` | `normal` |
| `delaySeconds` | Delay of direct operations in `timeout` | `10` |
| `retryAfterSeconds` | Value of `Retry-After` in `rate_limited` | `2` |
| `bookingOutcome` | `confirmed`, `pending`, `failed`: forces the outcome of new bookings | `null` (the fixture decides) |

The scenario affects **all** operations that touch that supplier: search, `GET /offers/{id}`, new bookings, replays and booking lookups.

| Mode | Search across several suppliers | Search for that supplier only | Direct operations |
| --- | --- | --- | --- |
| `unavailable` | warning `PROVIDER_UNAVAILABLE`, `partial=true` | `503` | `503`, without creating anything |
| `rate_limited` | warning `RATE_LIMITED`, `partial=true` | `429` + `Retry-After` | `429` + `Retry-After`, without creating anything |
| `timeout` | waits `MOCK_SUPPLIER_DEADLINE_SECONDS` and gives warning `PROVIDER_TIMEOUT` | `503` | responds successfully, but **after `delaySeconds`** |

**Lost response (US-08).** In `timeout` mode, `POST /bookings` **creates and persists the booking and then** waits `delaySeconds` before responding. A client with a shorter timeout does not see the response, but the booking exists and can be reconciled with `GET /v1/bookings/by-reference/{ref}` or by retrying with the same `Idempotency-Key`.

`GET /_control/scenario` lists the active scenarios and `DELETE /_control/scenario` clears them all.

### `/_control/clock`: controllable clock

```bash
curl -X POST $B/_control/clock -H 'Content-Type: application/json' -d '{"now": "2030-06-01T12:00:00Z"}'  # freezes
curl -X POST $B/_control/clock -H 'Content-Type: application/json' -d '{"advanceSeconds": 900}'       # expires the quotes
curl -X POST $B/_control/clock -H 'Content-Type: application/json' -d '{"now": "2030-06-01T12:00:00Z", "frozen": false}'  # shifts and keeps running
curl $B/_control/clock            # current state
curl -X DELETE $B/_control/clock  # back to system time
```

The clock governs `validUntil`, `searchedAt`, `createdAt` and `updatedAt`. By default it uses system time; since the fixtures are in 2030, they remain in the future.

### `POST /_control/booking/{id}/settle`: resolve a pending booking

```bash
curl -X POST $B/_control/booking/booking-000001/settle -H 'Content-Type: application/json' -d '{"status": "confirmed"}'
curl -X POST $B/_control/booking/booking-000001/settle -H 'Content-Type: application/json' \
  -d '{"status": "failed", "failure": {"code": "NO_INVENTORY", "message": "Host declined."}}'
```

Only a `pending` booking can be resolved; if it has already finished, it returns `409 BOOKING_NOT_PENDING`, because terminal outcomes are not reverted.

### `POST /_control/offers/{offerId}`: change an offer's terms

This control is my own addition to trigger `OFFER_CHANGED` and `OFFER_UNAVAILABLE` (US-07) in the middle of a flow. Any change generates a new revision and a new `validUntil`.

```bash
curl -X POST $B/_control/offers/offer-lis-hotel-central-p2 -H 'Content-Type: application/json' -d '{"price": "710.00"}'
curl -X POST $B/_control/offers/offer-lis-hotel-central-p2 -H 'Content-Type: application/json' -d '{"available": false}'
```

### `POST /_control/reset`

Deletes bookings, idempotency, references and quotes, and clears scenarios and clock. It is the "deliberate fixture reset" the contract mentions.

## Persistence and limits

- Data (bookings, idempotency, references and quotes) is written to a JSON file atomically (temporary file, `fsync` and `rename`) **before responding**, so it survives restarts and lost responses. Idempotency keys do not expire; the contract requires at least 7 days.
- Scenarios and the clock are **not** persisted: a restart clears them.
- The mock runs in **a single process with a single worker**. The "exactly one booking" guarantee under concurrent requests depends on that; with several workers it would not hold. The tests enable `yield_in_critical_section`, which yields the event loop between the key/reference check and the write; that way the concurrency tests fail if the lock is removed (verified: without the lock 20 and 10 bookings are created).
- It does not store tokens: each caller is identified by a hash of the token.
