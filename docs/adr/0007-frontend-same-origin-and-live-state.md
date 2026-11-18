# 0007. Same-origin frontend, SSE read with fetch, and a UI that is not the permission barrier

- **Status:** Accepted
- **Date:** 2026-11-17
- **Related stories:** US-01 … US-12 (flows in the UI), US-02, US-06, US-08

## Context

- The guide asks for a frontend that lets users complete the flows and understand their outcomes; "Visual polish is not the focus." It also asks for clear contracts between frontend and backend, understandable errors, and recovery after disconnections without assuming every notification arrived.
- The test identity travels in the `X-User-Id` header, and `EventSource` cannot send headers.
- The backend is already the one enforcing permissions (ADR 0001, US-02) and resolves concurrency with a `409` that carries the current state and the changes.

## Decision 1: React + Vite + TypeScript, no UI or state library

**Options considered**

- **Server-rendered HTML (Jinja + htmx).** Fewer pieces, but it mixes presentation into the backend, whose focus is the API, and complicates live per-tab state.
- **React with a component or state library** (MUI, Redux, React Query). They add little at this scale and add surface that has to be explainable.
- **React + Vite + TypeScript, with `fetch` and local state (chosen).** The contract types live in `src/types.ts`, written from the API serializers.

## Decision 2: a single origin, with nginx as the `/api` proxy

**Options considered**

- **CORS in the API.** Allowed origins and headers (`X-User-Id`) would have to be configured in every environment.
- **nginx serves the UI and proxies `/api` to the API (chosen).** In development the Vite proxy does the same. The `/trips/{id}/events` route is unbuffered and has a long read timeout, so events arrive immediately.

## Decision 3: SSE read with `fetch`, and events are notifications only

- `src/sse.ts` reads the stream with `fetch` (which does send `X-User-Id`) and an incremental parser. It reconnects honouring `retry`, and stops on `access-revoked`.
- On every (re)connection the backend sends `ready` and the UI **rereads with `GET`** the trip and the open tabs. A lost or duplicated event does not leave stale data: the displayed state always comes from a `GET`.

## Decision 4: the UI is not the permission barrier

- Actions are shown according to `myRole`, only to guide the user.
- The backend's `403`, `404` and `409` are shown **as returned**: status, code, message, details and `requestId`.
- The "Show actions even if my role does not allow them" option lets the demo check that the backend rejects them.

## Decision 5: a version 409 is resolved in a panel with reapply

- All versioned mutations go through `versioned(label, op)` (`src/TripView.tsx`).
- On a `409 TRIP_VERSION_CONFLICT`:
  - nothing is applied;
  - the view switches to the current state carried by the `409`;
  - the panel lists `changesSinceYourVersion` (version, who, when and what).
- The user can **reapply** the same operation on `currentVersion` or discard it. If another change happened in the meantime, the new conflict appears.
- There is no automatic merge: the user sees what changed before deciding.

## Consequences

- **Positive:**
  - the UI does not duplicate business rules;
  - what the UI displays always comes from a `GET`;
  - a single origin, with no CORS configuration.
- **Negative:**
  - local per-tab state (no shared cache), so some reads are repeated; at this scale it does not matter;
  - identity is still the test header, as in the API.

## Verification

- `frontend/src/*.test.ts(x)` (Vitest):
  - SSE parser and reconnection;
  - errors shown without rewriting;
  - only `confirmed` looks booked;
  - the four search outcomes look different;
  - 409 panel.
- `frontend/e2e/flow.spec.ts` (Playwright, with `scripts/e2e.sh` against Compose from scratch):
  - full flow;
  - `403` and `404`;
  - `409` with reapply and reread on reconnect;
  - partial, failed and empty search;
  - `pending` booking updated live.
