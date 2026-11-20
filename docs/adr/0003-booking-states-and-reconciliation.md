# 0003. Local booking states and reconciliation

- **Status:** Accepted
- **Date:** 2026-11-17
- **Related stories:** US-07, US-08 (also US-05 and US-09, which display these states)

## Context

US-08 requires distinguishing between pending, confirmed, failed and locally unknown; not creating duplicates on retry; reconciling unknown outcomes using the contract; distinguishing a failed request from an unresolved outcome; and never turning uncertainty into success or deleting confirmed bookings after an interruption.

The contract (`POST /bookings`, `GET /bookings/by-reference/{clientReference}`) provides:

- `Idempotency-Key` per caller: an identical retry returns the same booking (`200`), even if the quote has changed.
- Unique `clientReference`: a new key with an already accepted reference gives `409 REFERENCE_CONFLICT` and never creates another booking.
- `400`, `401`, `429` and `503` **do not create** a booking. A transport failure or a missing response **do not prove** rejection.
- `GET /bookings/by-reference` with `404` means there is no accepted record visible *at that moment*; an in-flight creation may still complete.
- The provider only knows `pending`, `confirmed` and `failed`. "Unknown" is a client-side state.

## Local states

| State | Meaning | Terminal? |
| --- | --- | --- |
| `draft` | Booking intent created locally with terms reviewed by the user (offer, revision, price). Not yet sent, or the last submission was not accepted and it is safe to retry. | No |
| `submitted` | The intent (with fixed `clientReference` and `Idempotency-Key`) was persisted **before** sending the `POST`; the request is in flight. | No |
| `unknown` | The `POST` was sent but we do not know whether the provider accepted it: timeout, dropped connection, 5xx without a contract error body, or a `submitted` left hanging by a restart. | No |
| `pending` | The provider accepted and its outcome is `pending`. | No |
| `confirmed` | The provider accepted and confirmed. | **Yes** |
| `failed` | The provider **accepted** the request and the booking later failed (`status=failed` with `failure`). | **Yes** |
| `rejected` | The provider **did not accept** the request: `409 OFFER_CHANGED` or `OFFER_UNAVAILABLE`, offer `404` or `400`. No booking exists at the provider. | **Yes** |

`failed` and `rejected` are kept apart on purpose: `failed` is a provider outcome on an accepted booking (with its `id` and its snapshot), whereas `rejected` means there never was a booking. For the user they imply different actions: in the first case, review the provider's failure; in the second, review the new terms and create a new attempt.

## Transitions

```
draft ──submit──▶ submitted ──201/200 pending──▶ pending ──▶ confirmed | failed
                     │  ──201/200 confirmed/failed──▶ confirmed | failed
                     │  ──409 OFFER_* / 404 / 400──▶ rejected
                     │  ──429 / 503 with contract Error──▶ draft   (not accepted; safe retry)
                     └─ timeout / network / 5xx without Error / restart ──▶ unknown
unknown ──(reconciliation only)──▶ pending | confirmed | failed | rejected
```

Rules:

1. **`confirmed`, `failed` and `rejected` never change.** The domain rejects any transition out of a terminal state. Retrying after a `rejected` creates **a new attempt** (new `clientReference` and new `Idempotency-Key`) with the terms the user reviews and accepts.
2. **Persist before sending.** `draft → submitted` is saved to the database before the `POST`. If the process dies at that moment, the record stays in `submitted`, and the key it was sent with is not lost.
3. **An old `submitted` becomes `unknown`.** If a record has been in `submitted` longer than the provider HTTP timeout plus a margin, the response is assumed lost (interruption) and it becomes `unknown`. Neither success nor rejection is ever assumed.
4. **`unknown` is left only through reconciliation**:
   1. `GET /bookings/by-reference/{clientReference}`:
      - `200` → the provider's state is adopted (`pending`, `confirmed` or `failed`), with its `id` and its snapshot.
      - `404` → it may still be in flight, so it stays `unknown` and is queried again later.
      - `429`, `503` or network error → it stays `unknown` (no outcome is invented) and is retried with backoff.
   2. If the `404`s continue past a **configurable grace period** (`BOOKING_UNKNOWN_GRACE_SECONDS`, counted from `submitted_at`), **the same `POST` is resent with the same `Idempotency-Key` and the same body**:
      - `201` or `200` → the returned state is adopted. If the original was accepted, the provider returns it instead of duplicating it.
      - `409 OFFER_CHANGED` / `OFFER_UNAVAILABLE` → `rejected`. Had the original been accepted, idempotency would have returned that booking; therefore it was not accepted.
      - `409 REFERENCE_CONFLICT` → an accepted booking exists with that reference, so go back to step 1.
      - `429`, `503` or network → it stays `unknown` and is retried later.
5. **`pending` is polled** with `GET /bookings/{id}` until it reaches `confirmed` or `failed`. If the query fails, the booking stays `pending` (the provider already accepted it); it is never downgraded to `unknown`.
6. **Accepted data is immutable.** When a provider booking is adopted, its `id`, the accepted offer and the price are saved and never overwritten with later provider data (US-05: confirmed prices remain understandable even if the provider's prices change).
7. **Every transition is recorded** with `updated_at` and `last_error` (code or message and the provider's `requestId`, if any) so it can be diagnosed.
8. **When returning to `draft` because of `429`/`503`, `last_error` is recorded** with the provider's code, message and `requestId` (`Booking.record_not_accepted`). *Pending for US-08:* show it to the user next to the draft, so they know why it was not sent and when to retry (for example, with `Retry-After`).

**Mapping to the adapter (added while implementing US-03).** `ProviderError.delivery_uncertain` decides between `unknown` and "not accepted":

- `True` (`ProviderTimeout`, `ProviderTransportError`, `ProviderProtocolError`): becomes `unknown`.
- `False`: `ProviderUnreachable` (no connection was made), `ProviderRateLimited` and `ProviderUnavailable` (with a contract `Error` body) go back to `draft`. `ProviderBadRequest`, `ProviderNotFound`, `OfferChanged` and `OfferUnavailable` become `rejected`.

**Implementation (2026-11-17, US-07/US-08).**

- **Before sending.** `POST /trips/{id}/itinerary/items/{itemId}/bookings` requires `expectedRevision` and `expectedPrice`, i.e. what the user reviewed. The backend fetches the offer again. If the revision or the price differ, or the offer is unavailable, it **sends nothing** and responds `409` (`TERMS_CHANGED` / `OFFER_UNAVAILABLE`) with `currentTerms`.
- **One active attempt per item.** Guaranteed by the partial unique index `uq_bookings_one_active_per_item` (states `draft`, `submitted`, `unknown`, `pending`, `confirmed`); a simultaneous double click gives `409 BOOKING_IN_PROGRESS`. `failed` and `rejected` do not block a new attempt.
- **`clientReference` and `Idempotency-Key`.** Generated per attempt and saved in `submitted` in a committed transaction **before** the `POST`. Retrying a `draft` (`POST /trips/{id}/bookings/{bookingId}/retry`) reuses the same key and the same body.
- **Immutability in the database.** A trigger (`bookings_guard`, migration 0004) rejects:
  - any state change out of `confirmed`, `failed` or `rejected`;
  - changing `provider_booking_id`, `accepted_offer` or `accepted_price` once set;
  - changing `client_reference` or `idempotency_key`.
- **`last_error`** is a JSON object (`code`, `message`, `providerRequestId`, `retryAfterSeconds`, `at`) that the bookings read returns together with `nextStep` (rule 8 fulfilled).
- **Rejection due to changed terms.** Saves the current offer, if it could be fetched, in `rejection_terms` so the user can see the new terms.
- **`REFERENCE_CONFLICT` on a first submission** becomes `unknown`: it indicates that a booking with that reference exists, and only reconciliation can find out which one.
- **Worker** (`python -m app.workers`, `worker` service in Compose):
  - processes the `jobs` table with `FOR UPDATE SKIP LOCKED` and a lease;
  - on every iteration, including at startup, schedules reconciliation of `submitted` records older than `BOOKING_SUBMITTED_STALE_SECONDS`;
  - the `sync_booking` job (deduplicated among active jobs) applies rules 3-5 and reschedules itself with backoff until it reaches a terminal state. There is no maximum number of attempts: giving up on reconciliation would turn uncertainty into silence.

**Note (2026-11-18): `Retry-After` in reconciliation.** The next attempt of a `sync_booking` job is scheduled with `retry_delay`: exponential backoff, but never earlier than the `Retry-After` of a provider `429`, even if it exceeds `BOOKING_RETRY_MAX_SECONDS`. It applies when polling a `pending`, when looking up an `unknown` by reference and when resending. Tests: `test_retry_delay_honours_retry_after_over_the_backoff` (unit), and `test_reconciliation_waits_for_the_providers_retry_after` and `test_a_rate_limited_resend_waits_for_retry_after` (against the mock).

**Note (2026-11-20): rate limit shared between processes.** The note above only made each job honour the `Retry-After` it received itself; another worker, or a user request, could still call the provider during that wait. Now:

- **Store.** Table `provider_rate_limits` (migration 0008), one row per provider with `not_before`. When any call (API, MCP or worker) gets a `429` with `Retry-After`, `not_before = GREATEST(not_before, now + Retry-After)` in one atomic upsert, committed on its own: a wait is never shortened, also with concurrent `429`s.
- **Check.** `RateLimitedProvider` (`app/domain/rate_limit.py`) wraps the provider client in the API and in the worker. Before each call it reads `not_before`; if `now < not_before`, it does not call and raises `ProviderWaitRequired`, a `ProviderRateLimited` (same code and meaning as a `429`: nothing was done) with the remaining seconds as `retryAfter`.
- **Worker** (reconciliation and resend). `sync_booking` turns `ProviderWaitRequired` into "try again at `not_before`": the job is rescheduled for that moment and the booking is not touched (no new `lastError`). Rules 4 and 5 are unchanged: an `unknown` stays `unknown` and a `pending` stays `pending`.
- **User requests** (search, offer re-check, booking, retry) do not wait: they answer at once with the existing rate-limit error and its `retryAfter`. A booking submission refused this way goes back to `draft`, as with a `429` (rule 8), with `lastError.retryAfterSeconds`.
- **Scope.** One row for the whole provider: the contract's `429` does not say which supplier was limited, and some calls (`GET /bookings/by-reference`) name none. A `429` without `Retry-After` (the contract requires it) is not stored. A `RATE_LIMITED` warning inside a partial search is not a `429` and is not stored either.
- **Discarded:** an in-process limiter (does not cover several workers or API replicas); Redis or another service (one more component for a single value; PostgreSQL is already shared); making user requests wait until `not_before` (it would hold the request for up to the `Retry-After`, which the guide asks to avoid).
- **Tests:** `tests/unit/test_rate_limit.py` (the decorator); `tests/integration/test_rate_limit.py`: two workers, one gets the `429` and the other does not call before `not_before` and reschedules for it; the stored wait is never shortened, also with concurrent upserts; a user search during the limit answers at once; and `test_not_accepted_goes_back_to_draft_and_retry_reuses_the_key`: a booking retry during the limit is refused without reaching the provider.

## Discarded options

- **Only `pending`/`confirmed`/`failed` (mirroring the provider).** Forces choosing a false state when we do not know whether it was accepted; violates US-08 ("do not turn uncertainty into success").
- **Treat a timeout as a failure and allow a retry with a new key.** Can create duplicate bookings if the original was accepted.
- **Resend the `POST` immediately instead of querying first.** It is safe thanks to idempotency, but if the terms have changed you get `OFFER_CHANGED`, and without querying first there is no cheap way to distinguish "it did not arrive" from "it arrived and we did not see it". Querying first answers the question without side effects; the resend is only used when the `404` persists.
- **Merge `failed` and `rejected`.** Hides whether a booking exists at the provider, which is exactly what the user needs to know.

## Consequences

- The state machine lives in the domain (`backend/app/domain/bookings.py`) and is tested with unit tests. Disallowed transitions raise `InvalidTransition`.
- Reconciliation is background work (worker), idempotent and resumable: it rereads the state on every attempt.
- New settings: `BOOKING_UNKNOWN_GRACE_SECONDS` and the margin for a hanging `submitted`.
- Contract dependency: concluding `rejected` from the resend relies on the provider resolving the `Idempotency-Key` before evaluating the terms, and on concurrent identical requests returning the same record ("Concurrent identical requests create exactly one booking; the others return that same record"). If the provider broke that with a creation still in flight, we could mark as `rejected` a booking that ends up accepted. We mitigate it with a generous grace period and, for the review, it is recorded as a known limitation.

## Verification

- Unit: all allowed and forbidden transitions; terminal states are immutable; `unknown` only leaves towards provider-backed states or towards `rejected`.
- Integration (US-08) against the mock: lost response (`timeout` mode), `404` then appearance, idempotent resend, `429` and `503` during reconciliation, `pending` → `settle`.
