# Architecture

Short explanation. The details of each decision, with its alternatives, are in the ADRs:

| ADR | Topic |
| --- | --- |
| [0001](adr/0001-version-based-concurrency.md) | Concurrency via optimistic trip version |
| [0002](adr/0002-provider-mock-with-control.md) | Provider mock with fixtures, out-of-contract controls and clock |
| [0003](adr/0003-booking-states-and-reconciliation.md) | Booking states and reconciliation |
| [0004](adr/0004-offer-snapshot-and-freshness.md) | Offer snapshot in the item and freshness policy |
| [0005](adr/0005-jobs-documents-and-status-channel.md) | Jobs in PostgreSQL, document as an immutable snapshot and SSE status channel |
| [0006](adr/0006-mcp-assistant-and-model-provider.md) | MCP in the same process, signed identity, own loop and swappable model |
| [0007](adr/0007-frontend-same-origin-and-live-state.md) | Same-origin frontend, SSE with `fetch` and a UI that is not the permission barrier |
| [0008](adr/0008-english-ui-and-docs-backend-messages-as-is.md) | English UI and docs; backend messages shown as returned |

## Components

```
browser ──HTTP──▶ nginx (frontend/) ──/api──▶ FastAPI API ──SQL──▶ PostgreSQL
                                                 │   ▲                ▲
                                     /mcp (MCP)  │   │ jobs           │
                                     assistant ◀─┘   │                │
                                                 │   worker ───────────┘
                                                 │     │
                                                 └─────┴──HTTP (openAPI.json)──▶ provider mock
```

- **Modular monolith with ports and adapters** (`backend/app/`).
  - `domain/` holds the rules: permissions, version, itinerary, costs, the booking and document state machine. It imports no frameworks; `tests/unit/test_architecture.py` checks this.
  - `ports/` declares what the domain needs: repositories, provider, storage, clock and model.
  - `adapters/` implements it: SQLAlchemy, httpx, disk, Gemini and MCP client.
  - `api/`, `mcp/` and `workers/` are entry points with no rules of their own.
- **API** (FastAPI): the test identity arrives in `X-User-Id`. The domain's `load_access` decides access: `404` for a non-participant, `403` if the role is insufficient.
- **Worker** (`python -m app.workers`): claims jobs from the `jobs` table with `FOR UPDATE SKIP LOCKED` and a lease. There are two kinds: reconcile bookings (`sync_booking`) and generate documents. It also sweeps interrupted submissions and orphan files. It can be scaled to several replicas.
- **PostgreSQL**: the state. Migrations with Alembic, a `trip_changes` history and triggers that make terminal states and the accepted data of bookings and documents immutable.
- **Provider mock** (`mock/`): a separate service implementing `assignment/openAPI.json`. The application only uses `/v1`; `/_control` is for tests and demos, and an architecture test prevents `backend/` from calling it.
- **MCP**: `MCPServer` from the official SDK, mounted at `/mcp`, with 7 read-only tools that reuse the use cases and `load_access`. The assistant is an MCP client with its own loop and uses Gemini through the `ChatModel` port.
- **Frontend**: React + Vite. nginx serves it and proxies `/api`. Live state arrives over SSE and is re-read with `GET`.

## Flow of a booking with a lost response

ADR 0003. Rehearsed in Docker (see [demo](demo.md), step 5).

1. **Terms review.** The editor sends `expectedRevision` and `expectedPrice`.
   - The API fetches the offer from the provider (`GET /offers/{id}`).
   - If the revision or the price changed, it responds `409 TERMS_CHANGED` with the current terms, without submitting anything.
   - If the offer is unavailable, it responds `409 OFFER_UNAVAILABLE`.
2. **Record before submitting.** The booking is stored as `submitted`, with its own `clientReference` and `Idempotency-Key`, and the transaction is committed. Nothing goes out to the provider without being recorded first. A partial index prevents two active attempts for the same item.
3. **Submission.** `POST /bookings`, and the provider creates the booking. Suppose the response never arrives: timeout, dropped connection or the API dies.
   - If the error says the request may have arrived (`delivery_uncertain`), the booking moves to `unknown`.
   - If the process died, it stays in `submitted`.
   - In no case does it appear as booked: `booked=false`, and `nextStep` says not to book again.
4. **Reconciliation.**
   - The worker schedules a `sync_booking` job (or the sweep detects a `submitted` older than `BOOKING_SUBMITTED_STALE_SECONDS`).
   - It first queries `GET /bookings/by-reference/{clientReference}`. If it exists, it adopts the provider's outcome (`confirmed`, `pending` or `failed`) and stores the accepted offer and price.
   - If it does not exist and the grace period has passed, it **resubmits with the same `Idempotency-Key`**: the provider returns the same booking or creates it only once.
5. **Notification.** The SSE channel announces the change and the UI re-reads with `GET`.

## Guarantees

| Guarantee | Mechanism | Evidence |
| --- | --- | --- |
| No edit is silently lost | Trip version with compare-and-set and `SELECT … FOR UPDATE`. The `409` carries the current state and `changesSinceYourVersion`. `403` comes before `409` | `tests/integration/test_us06_concurrency.py`; e2e of the 409 with re-apply |
| Permissions on every entry point | `load_access` in the API, SSE (re-checked on every poll), downloads and MCP tools. MCP identity travels in a signed token and is never an argument | `test_us02_sharing.py`, `test_status_channel.py`, `evals/deterministic/test_mcp_contract.py`, architecture tests |
| A booking is "booked" only if the provider confirmed it | State machine in the domain. `booked` is true only in `confirmed`, and a trigger prevents reverting terminal states | `tests/unit/test_booking_states.py`, `test_us07_us08_bookings.py`, Vitest and UI e2e |
| No duplicate bookings | Stable `Idempotency-Key` per attempt, one active attempt per item (partial index) and reconciliation by reference before resubmitting | `test_us07_us08_bookings.py` (double click, lost response, restart mid-submission) |
| A ready document is complete and matches its version | Immutable snapshot when requested, atomic write (temp file, `fsync` and `rename`), sha256 verified on every download, and regeneration from the snapshot if missing or mismatched | `test_us09_us10_documents.py`; demo rehearsal |
| Estimated versus confirmed price | The offer is copied into the item with `validUntil`. Costs separate estimated and confirmed, and the accepted price is immutable | `test_us04_itinerary.py`, `test_us05_costs.py` |
| Reproducible USD totals | `Decimal`, no floating point, *half-up* to cents, documented in `rounding` | `tests/unit/test_costs.py` |
| Recovery after disconnection | SSE events are only notifications; with `ready` on every (re)connection the UI re-reads with `GET` | `test_status_channel.py`; e2e "re-reads on reconnect" |
| Slow, down or rate-limited provider | Total deadline per call (`PROVIDER_TIMEOUT_SECONDS`), explicit partial or failed search, `lastError` and `nextStep` on bookings, and reconciliation never retries before `Retry-After` | `test_us03_search.py`, `test_provider_client.py`, `test_us07_us08_bookings.py` |
| A background operation can be traced | The request's `X-Request-Id` is passed to the job, and the worker uses it in its logs and in provider calls | `test_request_id_propagation.py` |
| The assistant does not make things up or write | Tools are read-only (architecture test). The instructions require data from the tools, and there are automatic checkers for prices, claims, dates, constraints, conflicts and gaps | `evals/` (deterministic by default; with a model, all 8 cases have passed: 5 of 5 on 2026-11-17, and 7 of 7 plus 1 of 1 on 2026-11-18) |

The tests were also checked by injecting faults: [`scripts/mutations/`](../scripts/mutations/README.md).

## Discarded alternatives (summary)

- **Concurrency:**
  - pessimistic locking or "last write wins": the first does not work for long edits and the second loses changes;
  - automatic merging: complex and opaque.
- **Job queue:** Redis/Celery or an external broker. One more piece to operate; PostgreSQL already provides transactions together with the state (ADR 0005).
- **Live state:** WebSockets or long-polling. SSE is enough for server-to-client notifications (ADR 0005).
- **MCP:** a separate MCP service, or calling the functions without MCP. The first duplicates authorization; the second violates US-12.
- **Assistant:** LangChain or LangGraph, or the SDK's *automatic function calling*. They hide the limits and the call log (ADR 0006).
- **Frontend:** CORS versus same origin, and UI or state libraries (ADR 0007).

## Known limits

- **Identity:** test header `X-User-Id`, no real authentication (the PRD allows this). MCP tokens are issued by the app only for the assistant; there is no issuance for external MCP clients.
- **Documents:** stored in a local volume shared by the API and the worker. In production they would go to object storage ([deployment](deployment.md)).
- **SSE:**
  - each stream queries PostgreSQL every `EVENTS_POLL_SECONDS`, which scales linearly with open connections;
  - the natural improvement is `LISTEN/NOTIFY`;
  - with several API replicas, the proxy for `/trips/{id}/events` must stay unbuffered.
- **Assistant:**
  - shares the process and connection pool with the API;
  - model evaluations are probabilistic: all 8 cases have passed with the real model (5 of 5 on 2026-11-17; on 2026-11-18, 7 of 7 and then the gaps case, 1 of 1), but each case only once: there is no pass rate and no measurement of token usage.
- **Reconciliation:**
  - concluding `rejected` after resubmitting depends on the provider honoring idempotency for identical in-flight requests (ADR 0003, documented risk);
  - `Retry-After` is respected per job, but there is no rate limiter shared between workers.
- **Quotes:** refreshed by hand, item by item (ADR 0004). Search results are not cached.
- **No cancellation or refunds** (out of scope). An item with an active booking cannot be removed.
- **Sample data:** the fixtures cover Lisbon, Madrid and Paris in June 2030, with simplified itinerary rules (`docs/assumptions.md`).
