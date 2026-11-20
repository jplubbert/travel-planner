# Assumptions and open questions

Record of ambiguities in the PRD (`assignment/prd.md`), the candidate guide and the provider contract (`assignment/openAPI.json`), and how they were resolved. Format of each entry:

> **Assumption:** … — **Affects:** US-XX / section — **Reason:** … — **Impact if wrong:** …

## General

## Identity and permissions (US-02, US-12)

> **Assumption:** Identity is for testing only: the `X-User-Id` header names one of the users preloaded by migration `0002` (`ana`, `beto`, `carla`, `dani`). Without the header, or with an unknown user, the response is `401`. — **Affects:** all stories — **Reason:** the PRD allows test identities and does not require an identity system. — **Impact:** in production it would be replaced by real authentication without touching the domain, because authorization already works with `user_id`.

> **Assumption:** Each trip has exactly one organizer, its creator. The organizer can only grant the `editor` and `viewer` roles, and their own role cannot be changed or removed (`409 ORGANIZER_ROLE_FIXED`). — **Affects:** US-02 — **Reason:** the PRD describes the organizer as the person who creates the trip and manages participants; transferring organizership is out of scope.

> **Assumption:** Editors can change the trip data (name, destinations, dates, travelers and budget), as well as the itinerary. Only the organizer manages participants. — **Affects:** US-02 — **Reason:** the PRD says "editors can perform planning actions; viewers cannot change the trip", and dates and budget are part of planning.

> **Assumption:** A non-participant receives `404 TRIP_NOT_FOUND` on every trip operation, with the same response as for a nonexistent trip or a malformed id; this way the trip's existence is not revealed. A participant without the required role receives `403`. — **Affects:** US-02, US-12.

> **Assumption:** Participant changes do not use version control: `PUT` sets the role (it is idempotent) and only the organizer, who is unique, can make them. — **Affects:** US-02/US-06 — **Impact:** two tabs of the same organizer changing the same person's role leave the last value; no edit by another participant is lost.

## Trips and itinerary (US-01, US-04, US-06)

> **Assumption:** In addition to destinations, dates and budget, a trip records the number of travelers (1 to 20, the range in the provider contract). — **Affects:** US-01, US-03 — **Reason:** provider search requires `travelers`, and each offer's price covers the whole group. The application's participants need not all be travelling (a viewer may not travel).

> **Assumption:** Trip dates cannot start before today (UTC). This is checked only when creating the trip or changing its dates, so a trip that has already started can still be edited. — **Affects:** US-01.

> **Assumption:** The budget is sent as a decimal string with up to 2 decimal places (`"1500"`, `"1500.5"`, `"1500.00"`) and stored as `numeric(12,2)`. JSON numbers are rejected to avoid floating-point errors. — **Affects:** US-01, US-05.

> **Assumption:** A trip has optional **preferences**: one free-text field (at most 500 characters after trimming, `""` by default and for trips created before migration 0007) for the group's preferences and restrictions, such as "private bathroom, no overnight journeys". It is edited like the other trip details: `PATCH` with `version`, it bumps the version, it appears in the change history ("Updated preferences") and errors are reported per field. Whoever can edit the trip (organizer or editor) can edit it; `""` clears it. It is not structured (no categories or flags) and the application does not filter or flag options with it: the assistant reads it with `get_trip` and relates the options to it. — **Affects:** US-01, US-11 — **Reason:** the PRD asks the assistant to relate options to "preferences and restrictions" without defining them; free text covers any preference without inventing a taxonomy, and a structured model can be added later.

> **Assumption:** Input errors return `422 VALIDATION_ERROR` with `details.fields[]` (field and message) and are all reported at once. An edit without `version` returns `400 VERSION_REQUIRED` (ADR 0001); the version is sent in the body (`"version": n`) and `GET` also exposes it in `ETag`. — **Affects:** US-01, US-06.

### Simplified itinerary rules (US-04)

The API returns these rules in `rules` on every itinerary read (source: `RULES` in `backend/app/domain/itinerary.py`):

1. Times are compared as absolute instants, using each offer's UTC offset.
2. Two transport or activity items conflict if their intervals overlap. One ending exactly when the next starts is not a conflict.
3. Accommodation only conflicts with other accommodation (one place per night). Activities and transport during a stay are expected.
4. Transfer times, connections, check-in windows and opening hours are not considered.
5. An item falls outside the trip if its local start date is before the first day of the trip or its local end date is after the last.
6. Every night, from the first day to the eve of the last, should have accommodation. Uncovered nights and destinations without accommodation are reported in `missing`.
7. With two or more destinations, they are visited in the order listed. Each pair of consecutive destinations needs a transport item from the first to the second (its origin and destination, in that direction); otherwise `missing` reports `MISSING_TRANSPORT` ("Missing transport from X to Y."). Travel to the first destination and back home is not checked.
8. A price is flagged as expired when its quote's `validUntil` has passed; the item must be refreshed to get the current terms. Items with a confirmed booking keep the accepted price.
9. Availability and price reflect the last quote (`quotedAt`) and may have changed since.

> **Assumption:** "Identifies missing information" includes missing transport between consecutive destinations (rule 7). The order of `destinations` is the travel order. A transport item counts when its origin and destination match the two destinations (case-insensitive) in that direction, whatever its date, availability or price state (those have their own flags). The way out to the first destination and the way home are not checked, because the trip does not record where the group starts. An empty itinerary only reports `EMPTY_ITINERARY`, as before. — **Affects:** US-04, US-11 (the assistant sees the gap through `get_itinerary`).

> **Assumption:** An unavailable offer cannot be added (`409 OFFER_UNAVAILABLE`), nor one quoted for a different number of travelers (`422`), nor the same offer twice in a trip (`409`). An offer may become unavailable or out of dates **after** being added, and is then flagged instead of removed. — **Affects:** US-04.

> **Assumption:** An item with an active booking (`submitted`, `unknown`, `pending` or `confirmed`) cannot be removed: it returns `409 ITEM_HAS_BOOKING`, because cancellation is out of scope and removing it would hide a real expense. It can be removed if its bookings are `draft`, `failed` or `rejected`; those bookings are kept with `itinerary_item_id = NULL`. — **Affects:** US-04, US-05, US-08.

> **Assumption:** Every itinerary mutation (add, remove, reorder, refresh quote) and trip edit share **a single version per trip** (ADR 0001). `DELETE` receives the version as the `?version=` parameter; the rest, in the body. Each accepted version leaves an entry in `trip_changes` (who, when, what), which is what the `409` lists in `changesSinceYourVersion`. — **Affects:** US-06.

## Search and provider (US-03, OpenAPI contract)

> **Assumption:** The provider rate limit is shared by the whole application and applies to the provider as a whole, not per supplier: a `429` with `Retry-After` on any call pauses every call (search, offers, bookings, lookups) from every API and worker process until `now + Retry-After`, keeping the longest wait. User requests during that time answer at once with `PROVIDER_RATE_LIMITED` and the remaining `retryAfterSeconds`, without calling the provider; the worker reschedules for that moment. — **Affects:** US-03, US-07, US-08, "slow or rate-limited providers" — **Reason:** the contract's `429` does not identify a supplier, and some calls (lookup by reference) do not name one; pausing everything is the conservative reading of "wait before another request". — **Impact:** a `429` for one supplier also pauses searches of the others for a few seconds (the mock's default `Retry-After` is 2). A `RATE_LIMITED` warning inside a partial search has no `Retry-After` and is not stored.

> **Assumption:** "Options fit the group" means each offer is quoted for the exact group size requested. The mock materializes each fixture per number of travelers (`offer-<fixture>-p<n>`) and excludes those that exceed their capacity. — **Affects:** `searchOffers` contract, US-03, US-05 — **Reason:** the contract says the price covers the whole group and that options must fit it. — **Impact if wrong:** low; it only changes how IDs are generated in the mock.

> **Assumption:** An offer fits the window if its local start date and its local end date are within `[startsOn, endsOn]`. For transport, departure uses the origin's offset and arrival the destination's. — **Affects:** `searchOffers` — **Reason:** the contract speaks of a "destination-local" window but does not specify which endpoints must fall inside. — **Impact if wrong:** an overnight flight arriving the day after the end of the window would be excluded.

> **Assumption:** A `providerId` that exists but does not offer the requested category returns `400`, same as an unknown one. A destination without fixtures returns an empty, complete `200`. — **Affects:** `searchOffers` — **Reason:** the contract only defines the unknown-supplier case. — **Impact if wrong:** the app must treat both as a request error, not a provider failure.

> **Assumption:** If all queried suppliers fail, the response is `503` even if all were rate-limited. Only a query to a single rate-limited supplier gives `429`. — **Affects:** `searchOffers` — **Reason:** it is the literal reading of "return 503 or 429 for a rate-limited single-supplier query".

> **Assumption:** A supplier scenario also affects replays and booking lookups for that supplier, not only search and new bookings. — **Affects:** reconciliation (US-08) — **Reason:** this way it can be shown that a failing reconciliation leaves the state as unknown instead of inventing it.

> **Assumption:** The application's search always uses the trip's number of travelers; it cannot be changed in the request. The search dates, defaulting to the trip's, must fall within them. The destination is free, so the trip home can also be searched, for example with destination London. — **Affects:** US-03 — **Reason:** offers must fit the group and the trip.

> **Assumption:** A failed search responds with the same body as the others (`outcome: "failed"`), with `error` and the failed suppliers, and with an HTTP error code: `504` for timeout, `502` if the provider response is invalid or the transport fails, and `503` otherwise (unavailable, rate limit with `Retry-After`, no connection or rejected credentials). — **Affects:** US-03.

> **Assumption:** If a search ends partial and with no offers (some suppliers fail and those that respond find nothing), it is classified as `partial`, not `empty`, because it is unknown whether the failed suppliers had options. — **Affects:** US-03.

> **Assumption:** Search results are not cached yet; each search queries the provider. `quoteExpired` is computed with the application's clock against the provider's `validUntil`. — **Affects:** US-03 — **Impact:** more calls to the provider; reusing results will be evaluated with US-04/US-07.

## Costs and rounding (US-05)

> **Rounding rules** (also returned in `rounding` in `GET /trips/{id}/costs`): provider prices and accepted prices are already exact cents, and they are summed with `Decimal`, no floating point and no intermediate rounding. Each returned amount is normalized to 2 decimal places with *half-up*, which changes nothing if they are already cents. `usedPercent = total / budget × 100` is rounded *half-up* to 1 decimal place, and omitted if the budget is 0. Tests in `tests/unit/test_costs.py` (0.10 + 0.20 + 0.30 = 0.60 exactly; 6.25 % → 6.3 %).

> **Assumption:** An item's cost is `confirmed` only if it has a `confirmed` booking; its `accepted_price` is then used. With a `pending`, `unknown` or `submitted` booking it remains an `estimate`, with a note of the state. With `failed` or `rejected` it goes back to the quote's estimate. The total sums estimates and confirmed amounts, which are also shown separately. — **Affects:** US-05.

> **Assumption:** Mock prices are computed as `base + per_traveler × travelers` with `Decimal`, rounding *half-up* to cents. The app never recomputes a provider price; it only sums already-rounded amounts. — **Affects:** US-05.

## Bookings (US-07, US-08)

> **Assumption:** If an offer is unavailable, the provider responds `OFFER_UNAVAILABLE` before comparing revision and price, even if the revision also changed. — **Affects:** `createBooking`, US-07 — **Reason:** the contract does not fix the precedence, and "unavailable" is the most useful information for the user.

> **Assumption:** A rejected booking request (4xx/5xx) does not record the `Idempotency-Key` or the `clientReference`; only accepted bookings do. — **Affects:** `createBooking`, US-07/US-08 — **Reason:** the contract says a rejection "does not create a booking" and that keys are resolved against accepted requests. — **Impact if wrong:** after an `OFFER_CHANGED` the app could reuse the key with new terms; even so, the app uses a new key for each attempt with different terms.

> **Assumption:** `clientReference` is unique per caller for the whole lifetime of the mock's dataset. — **Affects:** `createBooking` — **Reason:** the schema defines it as "unique within the caller's dataset".

> **Assumption:** Booking does not require the trip version (ADR 0001): it does not modify the trip or the itinerary, and its concurrency is protected by the one-active-attempt-per-item index. It does require the reviewed terms (`expectedRevision`, `expectedPrice`). — **Affects:** US-06, US-07.

> **Assumption:** If, when booking, the offer is quoted for a different number of travelers (because the trip's group changed), the response is `409 PARTY_SIZE_MISMATCH` without submitting anything. — **Affects:** US-07.

> **Assumption:** A `201` when booking means **an attempt was recorded**, not that it was booked. The state (`confirmed`, `pending`, `unknown`, `draft`, `failed` or `rejected`), `booked` (true only with `confirmed`) and `nextStep` say what happened. Rejections detected before submitting create no attempt and respond `409`. — **Affects:** US-07, US-08.

> **Assumption:** Reconciliation does not give up: a `sync_booking` job is rescheduled with backoff (up to `BOOKING_RETRY_MAX_SECONDS`) as long as the booking has not reached a terminal state. A down provider leaves the booking in `unknown` with `lastError`, never in an assumed success. — **Affects:** US-08.

> **Assumption:** Cancellation and refunds are out of scope (PRD). A `confirmed` booking prevents removing its item from the itinerary. — **Affects:** US-04, US-08.

> **Assumption:** A job created by the worker sweep (for example, a `submitted` booking whose submitter died) has no originating `X-Request-Id`, because the original request never got to enqueue anything. That booking is traced by its `clientReference` and by the provider's `requestId`s. — **Affects:** US-08, diagnostics.

## Documents (US-09, US-10)

> **Assumption:** The format is **self-contained HTML** (inline CSS, no scripts or external resources), downloaded as an attachment and printable to PDF from the browser. — **Affects:** US-09 — **Reason:** the PRD leaves the format open; HTML needs no dependencies, is deterministic and is verified by hash.

> **Assumption:** Requesting a document is an editor action (the PRD: viewers cannot "start document generation"); listing, viewing and downloading it, any participant. Retrying a `failed` one is an editor action. — **Affects:** US-02, US-09, US-10.

> **Assumption:** Requesting a document again returns the same one if one is in progress for the same trip version (`202`), or if there is a `complete` one whose content is identical, ignoring the snapshot time (`200`). If the trip or the state of any booking changed, a new one is created. — **Affects:** US-09.

> **Assumption:** If on download the file is missing or does not match its hash, the download itself, even when made by a viewer, returns the document to `waiting` and re-enqueues it to be regenerated from the snapshot. This is system maintenance, not a user change. Since rendering is deterministic, the same hash results. — **Affects:** US-10.

> **Assumption:** After `DOCUMENT_MAX_ATTEMPTS` generation failures the document stays `failed` with the error, and is not retried automatically. — **Affects:** US-09.

> **Assumption:** The status channel is per-trip SSE (`GET /trips/{id}/events`) and announces changes to the trip version, bookings and documents; it does not replay events after reconnecting. Clients must re-read with `GET` on receiving `ready` or any notification. — **Affects:** US-02, US-08, US-09.

## Assistant and MCP (US-11, US-12)

> **Assumption:** Any participant (including a viewer) can use the assistant chat (`POST /trips/{id}/assistant/chat`); the assistant acts with the permissions of the person asking and is always read-only. A non-participant receives `404` and the model is not even invoked. — **Affects:** US-02, US-11.

> **Assumption:** The chat does not store conversations: the client sends the recent history (`history`, up to 20 turns) with each request. — **Affects:** US-11 — **Reason:** avoids storing free text from users; the PRD does not require it.

> **Assumption:** MCP tools act only on what the session's user can see through the API. A `partial` or `empty` search is a normal result (with its `outcome`); a `failed` one, and any access or validation error, is a tool error (`is_error`), so that the model does not confuse it with "no results". — **Affects:** US-11, US-12.

> **Assumption:** Only the built-in assistant uses the MCP server; issuing MCP tokens to external clients is out of scope (the PRD does not require it). — **Affects:** US-12.

> **Assumption:** The assistant's responses are text in the user's language, with the tool calls made (`toolCalls`) attached for traceability. — **Affects:** US-11.

## Sample data and limits (destinations, simplified rules)

> **Assumption:** The fixtures cover Lisbon (10–14 June 2030), Madrid (14–18) and Paris (18–22). London only appears as a transport origin or destination. Outside those dates searches return an empty result. — **Reason:** the PRD allows limiting destinations and data as long as the behavior remains demonstrable. Details are in `mock/README.md`.

> **Assumption:** The mock uses a single worker; the "exactly one booking" guarantee under concurrent requests holds within that process. — **Reason:** it is a mock (see ADR 0002).

## Unresolved questions
