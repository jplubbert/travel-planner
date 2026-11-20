# Travel Planner

Take-home: a group travel planning platform. Shared trips with roles (organizer, editor, viewer), search against a mocked external provider, an itinerary with costs in USD, simulated bookings with reconciliation, downloadable documents, and a read-only AI assistant that uses MCP tools.

- Assignment (do not modify): [`assignment/`](assignment/) — [guide](assignment/candidate-guide.md), [PRD](assignment/prd.md), [provider contract](assignment/openAPI.json).
- Instructions for AI agents and working rules: [`CLAUDE.md`](CLAUDE.md).
- Architecture decisions: [`docs/adr/`](docs/adr/). Assumptions: [`docs/assumptions.md`](docs/assumptions.md).
- **Deliverable documentation:**
  - [architecture](docs/architecture.md): components, decisions, alternatives, guarantees and limits;
  - [deployment proposal](docs/deployment.md);
  - [demo script](docs/demo.md);
  - [AI usage](docs/ai-usage.md).

## Stack

Python 3.12 · FastAPI · SQLAlchemy async · PostgreSQL · Alembic · httpx · pytest · MCP (official SDK 2.x, `MCPServer`) · Google Gemini with `google-genai` (`gemini-3.8-flash`, no LangChain/LangGraph) · React + Vite + TypeScript (minimal UI, served by nginx) · Docker Compose. Modular monolith with ports and adapters; the provider mock is a separate service consumed only over HTTP.

## Structure

```
backend/app/
  domain/     core with no framework dependencies
  ports/      interfaces the domain needs
  adapters/   Postgres, provider HTTP client, storage…
  api/        HTTP (FastAPI)
  mcp/        read-only MCP server
  assistant/  assistant that consumes MCP
  workers/    background documents and reconciliation
mock/         simulated external provider (FastAPI)
frontend/     minimal UI (React + Vite + TypeScript)
scripts/      e2e.sh: Playwright against Compose started from scratch
tests/        tests and evaluations
docs/         ADRs and assumptions
```

## Getting started

```bash
cp .env.example .env           # optional: GEMINI_API_KEY for the assistant
docker compose up --build      # UI at http://localhost:5173 · API at http://localhost:8000/docs · mock at http://localhost:4010/v1
```

In the UI, the selector in the header switches between test users (`ana`, `beto`, `carla`, `dani`). Without `GEMINI_API_KEY` everything works except the assistant, which responds `503 ASSISTANT_UNAVAILABLE`.

## Tests

```bash
docker compose up -d db
uv sync && uv run pytest                               # backend, mock and deterministic evaluations
uv run pytest evals/llm -m llm -s                      # model evaluations: require GEMINI_API_KEY (cost in evals/README.md)
cd frontend && npm ci && npm test                      # Vitest
./scripts/e2e.sh                                       # full flow in Docker from scratch, with Playwright
```

Fault injection, to check that the tests catch real failures: `python3 scripts/mutations/run.py` (see [`scripts/mutations/README.md`](scripts/mutations/README.md), with the results of the latest run).

`scripts/e2e.sh` starts a separate Compose project, with its own volumes and ports, so it does not touch the local stack or its data. It builds all images, runs the Playwright tests through the frontend and removes the project when done. See [`frontend/README.md`](frontend/README.md).

See [`mock/README.md`](mock/README.md) for the fixtures and failure scenarios.

## Plan (4 days, due Friday 20 Nov 2026 23:59)

| Day | Date | Goal |
| --- | --- | --- |
| 1 | Tue 17 Nov | Complete **provider mock** per the contract (search with partial results, quotes with expiry/revision, idempotent bookings, lookup by reference, injectable failures, persistence). **Domain model** for trips and permissions, migrations. **US-01, US-02, US-03, US-04, US-05, US-06.** |
| 2 | Wed 18 Nov | **US-07, US-08** bookings (terms revalidation, idempotency, unknown state, reconciliation). **US-09, US-10** documents (background generation, states, recovery, authorized download). Read-only **MCP server**, **assistant**, **minimal frontend**. **Goal: the whole PRD working by end of day.** |
| 3 | Thu 19 Nov | Failure and concurrency tests, assistant evaluations (deterministic and with a model), README, architecture explanation, demo script. |
| 4 | Fri 20 Nov | Buffer, final review and submission. **Code freeze at 18:00.** |

Cross-cutting criterion: no story is considered done without tests that prove its acceptance criteria (see `CLAUDE.md`).

## Current status

| Area | Status |
| --- | --- |
| Repo scaffolding, CLAUDE.md, ADR 0001, Postgres in Compose | ✅ Done |
| Provider mock (`mock/`, ADR 0002): the contract's 5 operations, failure scenarios, clock, persistence; 99 tests validated against the schema | ✅ Done |
| Domain model and persistence (Trip, TripMember, ItineraryItem, Booking, Document, Job), Alembic migrations, test users | ✅ Done |
| Background job worker (`python -m app.workers`, `worker` service) | ✅ Done |
| ADR 0003: booking states and reconciliation (state machine in the domain, with tests) | ✅ Done |
| US-01: create a trip and return to it (persistence across restarts, actionable validation, versioned edits) | ✅ Done, with tests against Postgres |
| US-02: share with roles | ✅ Done: direct HTTP requests, documents, status subscriptions (SSE) and assistant tools (MCP), all tested |
| US-03: search and compare (complete / partial / empty / failed, freshness, estimates, timeout, permissions) | ✅ Done, with tests against the real mock over HTTP |
| US-04: itinerary (add with the provider's quote, remove, reorder, refresh quote; expired, unavailable, out-of-dates, overlap and group flags; missing information: nights and destinations without accommodation, and transport between consecutive destinations since 2026-11-20) | ✅ Done (ADR 0004) |
| US-05: per-item costs, totals and budget (estimated vs confirmed, documented rounding) | ✅ Done; also tested with real bookings (confirmed, pending, failed, rejected) |
| US-06: concurrent edits (per-trip version, 409 with current state and changes, 403 before 409) | ✅ Done, with concurrency tests against Postgres |
| US-07: book with the reviewed terms (re-check with the provider, 409 with current terms) | ✅ Done, with tests against the real mock |
| US-08: uncertain and partial outcomes (unknown/draft/rejected/pending/confirmed/failed, reconciliation worker, no duplicates) | ✅ Done, with tests against the real mock (lost response, restart mid-submission, double click, 429/503) |
| US-09: generate a document (202, immutable snapshot, worker with atomic write, states, retry, lease-based recovery) | ✅ Done (ADR 0005) |
| US-10: correct document (version and time, immutable against edits, hash-verified download, regeneration, orphan sweep) | ✅ Done (ADR 0005) |
| Per-trip SSE status channel (booking, document and trip notifications; same permissions; closes when access is lost) | ✅ Done (ADR 0005) |
| US-11: assistant with data-based recommendations (Gemini via adapter, own loop, limits, tool calls in the response; the trip's saved preferences since 2026-11-20, migration 0007) | ✅ Done (ADR 0006). Model evaluations with `gemini-3.8-flash`: 5 of 5 on 2026-11-17; on 2026-11-18, **7 of 7** at 01:08 and the gaps case **1 of 1** at 01:31, so **the 8 cases that existed then have passed with the real model**, across two runs; on 2026-11-20 all 9 cases ran 3 times each: 24 of 27, including the new preferences case 3 of 3. The 3 failures were a too-strict check, changed afterwards, and the 2 affected cases then passed 6 of 6 (`evals/README.md`). Tested by the candidate from the UI with a real key on 2026-11-17 ([`docs/demo.md`](docs/demo.md), step 7) |
| Traceability: the original request's `X-Request-Id` in worker jobs, in their logs and in the provider calls they make | ✅ Done (2026-11-18, migration 0006), with tests. Jobs created by the worker sweep have no originating request and are traced by `clientReference` |
| US-12: read-only MCP tools (official SDK, identity via signed session, user's permissions) | ✅ Done (ADR 0006), with deterministic evaluations |
| Frontend (ADR 0007): all flows, backend errors (403/404/409, validation) shown exactly as returned — status, code, message, details, requestId (ADR 0008) — 409 with re-apply, live state with re-read on reconnect | ✅ Done, with Vitest and Playwright against Compose from scratch |
| Deployment proposal, architecture explanation, demo script and AI usage note | ✅ Written ([`docs/`](docs/)). The deployment is only a proposal: it has not been deployed. [`docs/demo.md`](docs/demo.md) states which steps were rehearsed and which were not |

## Criteria not met or with partial evidence

Reviewed against the PRD and the guide on 2026-11-20. Everything else has automated tests (see "Current status").

| Criterion | Situation |
| --- | --- |
| US-11: "explains how the options fit dates, budget, **preferences** and **constraints**, and identifies **gaps or conflicts**" | **Nearly complete.**<br>• **Run:** on 2026-11-18 at 01:08, the candidate ran the evaluations and 7 of 7 passed with `gemini-3.8-flash` (97 s). Among them: relating options to dates or nights and mentioning the constraints of each proposed option, and pointing out an existing schedule conflict by naming both items. In the conflict case, the response also pointed out, unprompted, the 4 nights without accommodation and the lack of transport.<br>• **Run:** the **gaps** case (nights without accommodation), added later with its checker and deterministic tests. It passed 1 of 1 on 2026-11-18 at 01:31 (14 s).<br>• **Preferences (2026-11-20):** the trip stores them (optional free text, up to 500 characters, versioned like the other details), `get_trip` exposes them and the instructions ask the assistant to say whether each option meets them. The new checker and its composite check have deterministic tests (scripted model, good and deficient answers). The LLM case `test_relates_options_to_saved_preferences` **passed 3 of 3** with `gemini-3.8-flash` on 2026-11-20 (the answer kept the apartment and the hotel and left out the hostel "because it is a hostel and has a shared bathroom").<br>• **Missing transport (2026-11-20):** the app now computes it as a gap (US-04) and `get_itinerary` exposes it, with tests. Whether the model points it out is **not evaluated** with an LLM case |
| Model evaluations (guide) | Run by the candidate: 5 of 5 on 2026-11-17; on 2026-11-18, 7 of 7 at 01:08 and the gaps case 1 of 1 at 01:31. On 2026-11-20, all 9 cases with `EVAL_REPEATS=3` in one run: **24 of 27**. The 3 failures ("no option" 2 of 3, "failed search" 1 of 3) were correct answers that also said "0.00 USD used", taken from `get_costs`, and those two checks only accepted the budget. Probable cause: the new instruction to relate options to the saved preferences, which made the model summarise the trip context; not verified by isolating it. **Check change:** those two cases now accept any amount backed by the tool results, provided no offer was retrieved; an invented price still fails. Afterwards they passed **6 of 6**, but no answer in that rerun cited 0.00, so the relaxed path was not exercised. Token usage was not measured, and the documented cost is an estimate |
| Demo script (deliverable) | Written. On 2026-11-18 the candidate ran by hand from the UI the core of step 1: create the trip, search, add to the itinerary, refresh the quote, book (`confirmed`) and generate the document. Steps 5 and 6 were rehearsed through the API, not by clicking in the UI. Step 3 was rehearsed with Playwright, not by hand with DevTools. Step 7 was run by the candidate from the UI with a real key (see the table in [`docs/demo.md`](docs/demo.md)) |
| Fault injection in the provider mock | Done during development, but its script was not kept; it is recorded only in commit messages. US-01 to US-06 are covered again since 2026-11-20 by the `core` suite (16 of 16 killed). The suites in `scripts/mutations/` also cover bookings, documents, MCP and assistant, secrets, tracing, UI, trip preferences and missing transport, and the shared rate limit |
| Harness: tool configuration | **Partial.** Since 2026-11-20, `.claude/settings.json` is versioned with permissions: it allows `uv sync`, `uv run pytest`, `uv run ruff`, `uv run mypy`, `uv run alembic`, `pytest`, `ruff`, `mypy`, `npm`, `npx vitest`, `npx tsc`, `npx playwright`, `docker compose` and read-only `git` (status, diff, log, show, ls-files, blame, rev-parse), and asks before `git push` and `rm`. There are no hooks. Until then the default configuration was used, so the earlier work ran without it. `.gitignore` excludes a possible `.claude/settings.local.json`. `CLAUDE.md`, the `/story` prompt and the scripts are versioned |
| Deployment | Proposal only, as the guide allows; it has not been deployed |

## Proposed deployment

Summary. The details are in [`docs/deployment.md`](docs/deployment.md). **It has not been deployed**: only Docker Compose has been run locally.

- **Services:**
  - `frontend` (nginx with the static bundle and `/api` proxy) and `api` (FastAPI): stateless, several replicas behind a load balancer, with health at `/healthz` and `/readyz`;
  - `worker`: scales horizontally, because jobs are claimed with `FOR UPDATE SKIP LOCKED` and a lease;
  - a migrations job (`alembic upgrade head`) before each deployment.
- **Data:**
  - managed PostgreSQL with backups and point-in-time recovery;
  - documents in S3 or compatible, through an adapter for the `DocumentStorage` port (today it is a shared local volume).
- **Secrets:** `DATABASE_URL`, `PROVIDER_TOKEN`, `MCP_TOKEN_SECRET` and `GEMINI_API_KEY`, from a secrets manager. All configuration is read from the environment (`.env.example`).
- **Observability:** today there is end-to-end `X-Request-Id`, provider logs with its `requestId`, and `lastError` on bookings and documents. Still to add: queue and unresolved-booking metrics, alerts and traces.
- **For production:**
  - real identity (OIDC) instead of `X-User-Id`;
  - S3 for documents;
  - `LISTEN/NOTIFY` instead of polling PostgreSQL for each SSE stream;

## AI usage in development

Developed with Claude Code; the application's assistant uses Gemini, and these are different things. The versioned harness consists of:
- [`CLAUDE.md`](CLAUDE.md);
- the reusable prompt [`.claude/commands/story.md`](.claude/commands/story.md);
- [`docs/adr/`](docs/adr/) and [`docs/assumptions.md`](docs/assumptions.md);
- [`scripts/`](scripts/) (e2e and fault injection);
- [`evals/`](evals/).

The note on tools, models, how the work was directed, the AI errors detected and what the candidate decided is in [`docs/ai-usage.md`](docs/ai-usage.md).
