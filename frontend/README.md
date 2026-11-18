# frontend — minimal UI (React + Vite + TypeScript, no UI library)

It lets you complete the PRD flows and understand their outcome. Visual polish is not the goal. The decisions are in [ADR 0007](../docs/adr/0007-frontend-same-origin-and-live-state.md).

## Running

```bash
docker compose up --build      # from the repo root: UI at http://localhost:5173
```

In development, with the API running on `:8000`:

```bash
cd frontend && npm ci && npm run dev    # Vite proxies /api, just like nginx does in Compose
```

The selector in the header picks the test user (`ana`, `beto`, `carla`, `dani`), sent as `X-User-Id`.

## What each screen shows

- **Trips:** the trips you take part in, with your role, and trip creation.
- **Trip**, with the version, your role and the live channel status in the header. Tabs:
  - **Participants:** member management is shown only to the organizer. It also lets you edit the trip details.
  - **Search:** each outcome looks different:
    - complete (green);
    - partial (amber), with the providers that did not answer;
    - empty (neutral);
    - failed (red), with the code and `Retry-After`, never presented as "no options".
    - It also shows freshness (how long ago the result was received), the price as an estimate, validity, availability and restrictions.
  - **Itinerary:** warnings, schedule conflicts, expired prices, missing information, refresh quote, reorder and remove.
  - **Costs:** estimated versus confirmed, total, remaining budget and a warning when it is exceeded.
  - **Bookings:**
    - review the exact terms and confirm them (`expectedRevision` and `expectedPrice` are sent);
    - a `409 TERMS_CHANGED` shows the current terms and offers to refresh the quote;
    - each booking shows its status, `nextStep`, `lastError` and failure, and drafts can be retried.
  - **Documents:** generate, see the status and download. It warns when a document is from an older version.
  - **Assistant:** chat with the tools used (arguments, result and error) and the stop reason.

**UI rules**

- **Buttons follow the role, but the backend decides.** With "Show actions my role does not allow" every action is visible, and the backend's `403` responses are shown as returned: status, code, message and `requestId` (ADR 0008).
- **Version 409:** the panel lists the changes since your version (who, when and what), the view switches to the current state, and you can **reapply** the same change on the current version or discard it.
- **Live status:** SSE is read with `fetch`, because `EventSource` cannot send headers. Events are only hints: on every (re)connect `ready` arrives and the UI re-reads everything with `GET`.
- **Nothing looks confirmed unless it is.** Only a `confirmed` booking appears as booked (in green). `pending`, `submitted` and `unknown` say it is not booked yet; `unknown` also says not to book again. Costs keep estimated and confirmed apart.

## Tests

```bash
npm test                 # Vitest: SSE reader, HTTP client, status presentation, components
npm run typecheck
../scripts/e2e.sh        # Playwright against Docker Compose started from scratch (see below)
```

**What `scripts/e2e.sh` does**

- Starts a separate Compose project (`tp-e2e`, with its own volumes and ports 15173, 18000, 14010 and 55432), without touching the local stack or its data.
- Leaves `GEMINI_API_KEY` empty, so there are no model calls.
- Runs Playwright through nginx and removes the project when done. With `KEEP=1` it leaves it running.

**What the e2e covers**

- **Full flow:** create, share, search, add, costs, confirmed booking, downloaded document and assistant without a key (503).
- **Permissions:** `403` when forcing actions as a viewer and `404` for a non-participant.
- **Concurrency:** `409` with the changes and reapplying, with the live channel cut, and re-reading on reconnect.
- **Search:** partial, failed and empty outcomes.
- **`pending` booking:** it does not look confirmed until the provider confirms it, and the change arrives live.

To check that the tests catch errors, 11 mutations were injected into the UI, and every one makes some test fail:
- the e2e catches the flow ones (re-reading on reconnect, reapplying on the current version, booking events, terms sent);
- Vitest catches the presentation ones (for example, `unknown` looking confirmed).
