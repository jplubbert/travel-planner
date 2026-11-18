# backend — Main application (FastAPI + async SQLAlchemy), modular monolith with ports and adapters

## Structure

- `app/domain`: entities, rules and use cases, without frameworks.
- `app/ports`: protocols (repositories, clock, provider).
- `app/adapters`: SQLAlchemy/Postgres, provider HTTP client and system clock.
- `app/api`: HTTP.
- `migrations/` + `alembic.ini`: schema and test data. The container runs `alembic upgrade head` before serving.

## Pieces

- **Identity and permissions:** identity is for testing only, with the `X-User-Id` header (`ana`, `beto`, `carla`, `dani`). `require_role` (in `app/api/deps.py`) uses the domain's `load_access`. A non-participant gets `404` (same as a nonexistent trip) and a participant with an insufficient role, `403`. The domain use cases check the role again.
- **Provider:** port `app/ports/provider.py` (`ProviderClient`) and adapter `app/adapters/provider_http.py`. The adapter uses httpx with an overall deadline per call (`PROVIDER_TIMEOUT_SECONDS`). Each failure becomes a distinct domain `ProviderError` (`app/domain/provider.py`). Each call is logged with the provider's `requestId` and the request's own.
- **Search (US-03):** `app/domain/search.py`. Responds with `outcome` = `complete` | `partial` | `empty` | `failed`, with `failedSuppliers`, `searchedAt`, `receivedAt` and a `disclaimer`. Each offer includes `priceType: "estimate"`, `validUntil`, `quoteExpired`, `available` and `restrictions`.
- **Itinerary and costs (US-04/05/06):**
  - `app/domain/planning.py`: versioned mutations with history in `trip_changes`.
  - `app/domain/itinerary.py`: flags and rules (pure functions).
  - `app/domain/costs.py`: breakdown and rounding (pure functions).
- **Bookings (US-07/08, ADR 0003):**
  - `app/domain/bookings.py`: state machine and `nextStep`.
  - `app/domain/reservations.py`: use cases and reconciliation.
  - `app/workers/worker.py`: worker for the `jobs` table (bookings and documents), started with `python -m app.workers`.
  - The database guarantees one active attempt per item and that accepted data is immutable (partial index and trigger, migration 0004).
- **Documents (US-09/10, ADR 0005):**
  - `app/domain/document_service.py`: snapshot, request, generation, verified download and sweep.
  - Ports `app/ports/documents.py` (`DocumentStorage`, `DocumentRenderer`) with their adapters `local_storage.py` (disk, atomic write) and `document_html.py` (deterministic HTML).
  - Files live in `DOCUMENT_STORAGE_DIR`, a `documents` volume shared by `api` and `worker`.
- **MCP and assistant (US-11/12, ADR 0006):**
  - `app/mcp/server.py`: `MCPServer` from the official SDK, mounted at `/mcp`, with 7 read-only tools.
  - `app/mcp/identity.py`: signed session token; identity is never a tool argument.
  - `app/assistant/loop.py`: our own loop.
  - Ports `app/ports/assistant.py` (`ChatModel`, `AssistantTools`) with the adapters `gemini_model.py`, `scripted_model.py` and `mcp_client.py` (official MCP client over HTTP, in the same process).
- **Status channel:** `GET /trips/{id}/events` (SSE). `app/domain/status_feed.py` checks access again on every poll.

## Endpoints

"Viewer" includes editors and organizers, and "Editor" includes the organizer. Mutations marked *version* require the trip version (ADR 0001).

| Method and path | Minimum role | Notes |
| --- | --- | --- |
| `GET /me`, `GET /users` | test user | current identity; user directory |
| `POST /trips` | test user | whoever creates the trip is its organizer |
| `GET /trips` | test user | only the trips they participate in |
| `GET /trips/{id}` | Viewer | includes `version` and `ETag` |
| `PATCH /trips/{id}` | Editor | *version* in the body |
| `GET /trips/{id}/members` | Viewer | |
| `PUT /trips/{id}/members/{userId}` | Organizer | grants `editor` or `viewer` |
| `DELETE /trips/{id}/members/{userId}` | Organizer | |
| `POST /trips/{id}/offers/search` | Viewer | US-03 |
| `GET /trips/{id}/itinerary` | Viewer | flags, missing information and rules |
| `POST /trips/{id}/itinerary/items` | Editor | *version*; `offerId` (provider price) |
| `DELETE /trips/{id}/itinerary/items/{itemId}?version=` | Editor | *version* in the query |
| `PUT /trips/{id}/itinerary/order` | Editor | *version*; `itemIds` |
| `POST /trips/{id}/itinerary/items/{itemId}/refresh` | Editor | *version*; refreshes the quote |
| `GET /trips/{id}/costs` | Viewer | US-05 |
| `POST /trips/{id}/itinerary/items/{itemId}/bookings` | Editor | US-07; `expectedRevision` and `expectedPrice`. `201` records an attempt (see `status`); `409` if the terms changed, without sending anything |
| `POST /trips/{id}/bookings/{bookingId}/retry` | Editor | `draft` only; same `Idempotency-Key` |
| `GET /trips/{id}/bookings` | Viewer | status, `booked`, `lastError`, `nextStep`, accepted offer and price |
| `GET /trips/{id}/bookings/{bookingId}` | Viewer | |
| `POST /trips/{id}/documents` | Editor | US-09; `202` with the document in `waiting` (or the equivalent existing one) |
| `GET /trips/{id}/documents`, `GET /trips/{id}/documents/{docId}` | Viewer | status, version, snapshot time, sha256, `downloadUrl` when ready |
| `POST /trips/{id}/documents/{docId}/retry` | Editor | `failed` only |
| `GET /trips/{id}/documents/{docId}/download` | Viewer | verifies the hash; if the file is missing or does not match, `409` and it is regenerated |
| `POST /trips/{id}/assistant/chat` | Viewer | US-11; `message` and `history`. Responds with `answer`, `toolCalls`, `stopReason` (`answered`, `iteration_limit` or `time_limit`); `503` without `GEMINI_API_KEY`, `502` if the model fails |
| `POST /mcp/` | MCP token | MCP server (streamable HTTP); tools: `list_my_trips`, `get_trip`, `get_itinerary`, `get_costs`, `get_bookings`, `get_documents`, `search_offers` |
| `GET /trips/{id}/events` | Viewer | SSE: `ready`, `trip`, `booking`, `document`, `access-revoked`; they are notifications, reread with `GET` |
| `GET /healthz`, `GET /readyz` | — | process liveness; database reachable |

Interactive OpenAPI at `/docs`.
