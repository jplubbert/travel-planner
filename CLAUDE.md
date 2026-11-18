# CLAUDE.md

Instructions for AI agents (Claude Code or others) working in this repository.

## Project summary

Take-home: a group travel planning platform. An organizer creates a trip (destinations, dates, budget in USD) and shares it with editors and viewers; options are searched in an external provider (transport, accommodation, activities), an itinerary is built, simulated bookings are made, downloadable documents are generated, and a **read-only** AI assistant recommends options using tools exposed over MCP.

Sources of truth (in `assignment/`, **never modify**):

- `assignment/candidate-guide.md` — constraints, evaluation criteria, deliverables.
- `assignment/prd.md` — user stories US-01 … US-12 and acceptance criteria.
- `assignment/openAPI.json` — external provider contract. It is mocked and consumed only over HTTP.

Deadline: **Friday 20 Nov 2026, 23:59**.

## Decisions already taken

- **Backend:** Python 3.12, FastAPI, SQLAlchemy (async), PostgreSQL, Alembic, httpx, pytest.
- **Architecture:** modular monolith with ports and adapters (hexagonal).
  - `backend/app/domain/` is the core: it **does not import FastAPI, SQLAlchemy or httpx** (or any other I/O framework).
  - `backend/app/ports/` defines Python interfaces (`typing.Protocol` or ABC) that the domain needs.
  - `backend/app/adapters/` implements those ports (Postgres, provider HTTP client, file storage, clock…).
  - `backend/app/api/`, `backend/app/mcp/` and `backend/app/workers/` are entry points that orchestrate use cases; they contain no business rules.
- **Provider mock:** separate service in `mock/` (FastAPI). The application consumes it **only over HTTP** according to `openAPI.json`; never its database or its control endpoints.
- **MCP:** server built with the official Python SDK (`mcp` 2.x, where **FastMCP is called `MCPServer`**), **read-only**, mounted in the API at `/mcp` (streamable HTTP). It does not expose itinerary modification or bookings. Identity travels in a signed session token, never as a tool argument (ADR 0006).
- **Assistant:** Google Gemini with the official `google-genai` SDK, behind the `ChatModel` port (swappable adapter; `ScriptedModel` for tests). Default model `gemini-3.8-flash`, verified in Google's documentation on 2026-11-17. The tool-use loop is our own (`app/assistant/loop.py`): the assistant is an MCP client, discovers the tools with `list_tools` and passes them to the model. **No LangChain or LangGraph** or other agent frameworks. Configuration via environment: `GEMINI_MODEL`, `GEMINI_API_KEY` (ADR 0006, which supersedes the earlier decision to use Anthropic).
- **Frontend:** React + Vite + TypeScript in `frontend/`, no UI library. nginx serves it and proxies `/api` (same origin). SSE is read with `fetch` and state is re-read with `GET` on every reconnection. The UI is not the permission barrier: the backend's 403 and 409 are shown as returned (ADR 0007). The UI is in English, roles are shown as organizer/editor/viewer, and backend messages are shown as returned (ADR 0008).
- **Infrastructure:** Docker Compose for everything.

## Working rules

1. **Read the PRD before each story.** Before starting a US, reread its section in `assignment/prd.md` (and the relevant parts of `openAPI.json` and the candidate guide). Quote the acceptance criteria that will be covered.
2. **Nothing is complete without a test that proves it.** Do not mark a story, criterion or task as done if there is no automated test verifying the claimed behavior. If something cannot be tested, say so explicitly and document why.
3. **Assumptions in `docs/assumptions.md`.** Every ambiguity in the PRD or the contract, and how it was resolved, goes there, with the story or section it affects.
4. **Architecture decisions in `docs/adr/`.** Every relevant decision (real alternatives, trade-offs) is recorded as a numbered ADR according to `docs/adr/README.md`. Do not change an accepted decision without a new ADR that supersedes it.
5. **Do not modify `assignment/`.** Not the format, not whitespace, not the OpenAPI contract.
6. **Respect the architecture boundaries.** If the domain needs something external, define a port. Never import adapters from the domain.
7. **Honesty about status.** If a requirement is left incomplete, document what is missing and its impact in the README; do not present it as finished.
8. **Configuration outside the code.** Every environment-dependent value is read from environment variables; document them in `.env.example`.
9. **Never build images or run tests, rehearsals or demos while mutations are running.** `scripts/mutations/run.py` modifies repo files during each mutation. Any `docker compose build`/`up --build`, `pytest`, `npm test`, Playwright or rehearsal launched in parallel may read mutated code and give false results. Wait for it to finish, check that `git diff -- backend/ frontend/src` is empty and that there are no `.orig` files, and only then continue. It already happened once: a demo rehearsal ran on an image built with `if False:` in `reservations.py` and had to be repeated (see `docs/demo.md`).
10. **Everything in English.** All repository text (docs, ADRs, UI, comments, test names) is in English. `assignment/` is never modified.

## Commands

```bash
cp .env.example .env
docker compose up -d --build   # Postgres, API, worker, mock on :4010 and UI on :5173
uv sync                        # Python 3.12 environment (.venv)
uv run pytest                  # tests and deterministic evaluations (excludes `llm`); needs `db` running
uv run pytest evals/llm -m llm -s   # model evaluations; key from GEMINI_API_KEY or .env (cost: evals/README.md)
uv run mypy backend/app        # types (strict)
uv run ruff check .            # lint
cd frontend && npm test        # Vitest (and npm run typecheck)
./scripts/e2e.sh               # Playwright against a separate Compose started from scratch (no Gemini key)
python3 scripts/mutations/run.py   # fault injection; NOTHING else in parallel (rule 9)
```

Migrations: `cd backend && DATABASE_URL=... uv run alembic revision --autogenerate -m "..."`, always reviewing the result. `tests/integration/test_persistence.py` fails if the models and the migrations diverge. Test identity: `X-User-Id` header (`ana`, `beto`, `carla`, `dani`).

The mock is documented in `mock/README.md`. Its `/_control/*` endpoints are used only from tests and demos; code in `backend/` must never call them.
