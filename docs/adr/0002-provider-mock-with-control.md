# 0002. Provider mock with base fixtures, out-of-contract controls and a controllable clock

- **Status:** Accepted
- **Date:** 2026-11-17
- **Related stories:** US-03, US-07, US-08 (and all the failure evidence)

## Context

- `assignment/openAPI.json` defines the external provider: multi-supplier search with partial results, quotes with `revision` and `validUntil`, bookings that are idempotent per caller, `REFERENCE_CONFLICT`, `OFFER_CHANGED`, `OFFER_UNAVAILABLE`, 429/503 and reconciliation by `clientReference`. It requires bookings, references and idempotency to survive normal provider restarts, and fixtures to use future dates **or a documented controllable clock** so that validity and expiry are reproducible.
- The guide asks for **"reproducible failure scenarios"** and "checks that can detect real failures rather than only verify prepared mock responses", and a demo of failures and concurrency.
- The guide also says the application must consume the provider **through its public contract, not through its database or its test controls**, and that the control endpoints are outside the contract.

We therefore need a mock that is faithful to the contract, deterministic for tests and demos, and able to inject failures, without the application being able to couple itself to that capability.

## Options considered

### A. Random failures (configurable probability of 503/timeout/429)

- ✅ Trivial to implement; resembles the real world.
- ❌ Not reproducible: a test or a demo can pass or fail depending on luck.
- ❌ Impossible to pin down "supplier-b down while supplier-a responds" to demonstrate partial results at will.

### B. Fixed fixtures only (hard-coded data and behaviours, no runtime control)

- ✅ Deterministic and simple; no extra surface.
- ❌ Each scenario needs a "trap" fixture (an offer that always fails, a supplier that is always down), which pollutes the normal data.
- ❌ Cannot expire a quote or change a price in the middle of a flow (US-07) without waiting in real time.

### C. Control endpoints (outside the contract) on top of base fixtures

- ✅ Deterministic and on demand: the test or the presenter triggers exactly the failure they want to demonstrate and turns it off afterwards.
- ✅ The base data stays "normal".
- ❌ Extra surface that the application could misuse if it is not clearly separated.

## Decision

**Base fixtures + `/_control/*` endpoints outside the contract + controllable clock.**

- **Deterministic base fixtures:** destinations Lisbon, Madrid and Paris (June 2030), three logical suppliers (`supplier-a`, `supplier-b`, `supplier-c`), at least 4 offers per category and destination. Some fixtures have their own behaviour (an unavailable offer, a booking that stays `pending`, a booking that ends `failed`) to cover those cases without triggering anything.
- **Contract under `/v1`, controls under `/_control`:** separate paths, no provider token, documented only in `mock/README.md`. The application only knows `PROVIDER_BASE_URL` (which points to `/v1`); no application or assistant code may call `/_control` (to be verified with an architecture test in the backend).
- **Controls:** `/_control/scenario` (per supplier: `unavailable`, `timeout`, `rate_limited`, and a forced booking outcome), `/_control/clock` (set/advance the time), `/_control/reset`, `/_control/booking/{id}/settle` (pending → confirmed/failed) and `/_control/offers/{id}` (change price/availability to trigger `OFFER_CHANGED`/`OFFER_UNAVAILABLE`).
- **Controllable clock:** all of the mock's time-based logic (`validUntil`, `searchedAt`, `createdAt`, `updatedAt`) uses an injected clock. By default it is the system time; tests and the demo freeze or advance it.
- **Persistence:** the data state (bookings, idempotency, references, quotes) is saved to a JSON file with an atomic write **before** responding. The control state (scenarios, clock) is ephemeral: a restart clears it.

## Consequences

**Positive**

- Every failure scenario in the demo and the tests is reproducible with an explicit call.
- Quote expiry is tested in milliseconds by advancing the clock.
- The `timeout` mode applied to bookings **creates the booking and delays the response**, which is exactly the "lost response" case of US-08.

**Negative / risks**

- The mock is a single process with an in-memory lock: the guarantee of "exactly one booking for concurrent identical requests" holds for a single replica. That is enough for a mock; it is documented.
- Persisting a full JSON file on every write does not scale; irrelevant at a mock's volume.
- The controls are a back door: mitigated with separate paths, documentation and a test that prevents their use from the application.

**Verification**

- Mock tests (`mock/tests/`) that validate every response against the schemas in `assignment/openAPI.json` with `jsonschema` (Draft 2020-12, with format checking), including concurrent idempotency, persistence across restarts and lost responses.
