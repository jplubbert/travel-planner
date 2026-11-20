# AI usage in development

## Tools and models

| Use | Tool | Model |
| --- | --- | --- |
| Development (code, tests, documentation, Docker rehearsals) | Claude Code (CLI) | Claude Opus 5.5 (`claude-opus-5-5`), the model reported by the environment of the working sessions |
| Application assistant (US-11) | `google-genai` SDK | `gemini-3.8-flash`, verified in Google's documentation on 2026-11-17 (ADR 0006) |

These are different things: Claude Code is the tool the project was built with, and Gemini is the model the assistant uses inside the application. The repository does not depend on Claude at runtime.

## The versioned harness and how to use it

| Piece | Purpose |
| --- | --- |
| [`CLAUDE.md`](../CLAUDE.md) | Agent instructions: summary, decisions taken, working rules and commands. Claude Code loads it automatically when the repo is opened. |
| [`.claude/commands/story.md`](../.claude/commands/story.md) | Reusable prompt: `/story US-07` in Claude Code runs the cycle for a story (PRD → tests → assumptions and ADR → fault injection → checks → commit). It was written at the end, **distilled** from the per-story prompts that were used. During development those prompts were written by hand in each session. |
| [`docs/adr/`](adr/) and [`docs/assumptions.md`](assumptions.md) | Memory of decisions and assumptions. The agent must record what it decides there, and cannot change an accepted decision without a new ADR. |
| [`scripts/mutations/`](../scripts/mutations/README.md) | Fault injection to check that the tests catch real failures. |
| [`scripts/e2e.sh`](../scripts/e2e.sh) | Full flow in Docker from scratch, with Playwright. |
| [`evals/`](../evals/README.md) | MCP and assistant evaluations: deterministic, and model-based ones marked `llm`. |

**Requirements to use it:**
- Claude Code with access to an Anthropic model;
- Docker with Compose;
- `uv` (Python 3.12);
- Node and npm (tested with Node 26, the Docker image's version);
- for model evaluations, a Gemini key.

Claude Code configuration: until 2026-11-20 the default configuration was used. Since then [`.claude/settings.json`](../.claude/settings.json) is versioned with permissions (it allows the test, lint, type-check, `docker compose` and read-only `git` commands, and asks before `git push` and `rm`); there are no hooks. `.gitignore` excludes `.claude/settings.local.json`, which is machine-specific.

## How the work was directed

- **Framework first, no code.** The first session only created `CLAUDE.md`, ADR 0001, the assumptions, the skeleton, Compose and the README, with the explicit instruction to read the full guide, PRD and contract and to **never modify `assignment/`**.
- **One prompt per story or group of stories**, with the criteria to cover and the candidate's conditions. For example (originally in Spanish): "US-07 and US-08 per ADR 0003, with the detailed list; repeat the US-05 tests with real bookings; inject faults; commit". Each prompt required rereading the PRD, tests proving each criterion, assumptions and ADR, and a commit at the end (rules in `CLAUDE.md`).
- **Tests against real dependencies:**
  - real PostgreSQL;
  - the provider mock as a real HTTP process;
  - a real uvicorn for SSE;
  - in the frontend, Playwright against Compose.
  - Failures are triggered with `/_control` only from tests, and an architecture test prevents the backend from calling it.
- **Fault injection in every story.** After each block, deliberate faults were introduced (removing a check, changing a state, not persisting before submitting…) and it was checked that some test failed. When a mutation survived, the missing test was written or it was justified as defense in depth, verified by removing both layers. Current results are in [`scripts/mutations/README.md`](../scripts/mutations/README.md).
- **Docker rehearsals** in addition to tests:
  - killing the API halfway through a booking;
  - deleting a document's file;
  - an external MCP client over a real network.
- **Candidate review** of every agent delivery. Several corrections came from that review (below).

## AI errors detected and corrected

**Detected by the tests or by the agent itself**

| Error | How it was detected | Fix |
| --- | --- | --- |
| Under READ COMMITTED, a later validation could see a newer commit and respond `422` instead of `409` | Intermittent concurrency test | `SELECT … FOR UPDATE` in the version check, and a deterministic test (ADR 0001) |
| `JSONB` stored `None` as JSON `null`, so `IS NOT NULL` was true | Integrity tests | `JSONB(none_as_null=True)` and normalization in migration 0004 |
| `ON CONFLICT` with a parameterized partial-index predicate failed on the sixth execution (asyncpg generic plan) | Job queue test | Literal predicate in `index_where` |
| `mcp.server.fastmcp` was used, which no longer exists in SDK 2.x (now `MCPServer`), as well as camelCase attributes | Import and attribute errors | `MCPServer` and snake_case fields; `CLAUDE.md` corrected |
| A `ModelError` inside the MCP client's task group arrived wrapped in an `ExceptionGroup` and gave `500` | Deterministic eval | Caught inside the MCP context and answered with `502` |
| Mutation tests for the first stories that did not detect a missing lock | Surviving mutation | Test with `yield_in_critical_section` |
| While drafting `docs/deployment.md` it was claimed that reconciliation respects `Retry-After` | Review against the code before the commit | Corrected in the document. Later, at the candidate's request, it was implemented: reconciliation no longer retries before `Retry-After` |
| A UI mutation (SSE parser) left syntactically invalid code, so it was "detected" by a compilation error and not by a test | Reading the runner's output | Mutation rewritten to be valid; the tests now detect it |
| The `pending` booking e2e reopened the tab after confirming, and the re-read could hide a lost SSE event: the "ignore booking events" mutation was detected one day and not another | Re-running the mutation suite with the new runner | Test fixed so that only a live event can update the card; detected in 2 of 2 runs |

**Detected by an independent review (2026-11-20)**

| Error | Fix |
| --- | --- |
| `docker-compose.yml` passed api and worker only some of the settings `config.py` reads: values in `.env` such as `PROVIDER_TIMEOUT_SECONDS`, the worker and document timings, `MCP_ALLOWED_HOSTS` or the assistant limits never reached the containers | `env_file` (optional) plus the Docker-specific overrides; `.env.example` marks the host-only values; `tests/unit/test_compose_config.py` checks it with `docker compose config` |
| A booking and a removal of the same item could cross: the removal committed while the booking re-read the offer, and recording the booking failed with a foreign-key `500` | The booking re-checks the item under the trip row lock every itinerary change holds; two concurrent integration tests, one failing before the fix |
| A viewer's download of a damaged document changed its state and enqueued work, although viewers are read-only | `409 DOCUMENT_NEEDS_REGENERATION` for viewers; the storage sweep now also detects corrupt files (ADR 0005, note of 2026-11-20) |
| `send()` used a default `ReconcilePolicy` instead of the configured one, so `BOOKING_PENDING_POLL_SECONDS` was ignored for bookings sent by the API | The configured policy is passed through; integration test on the job's `run_after` |
| `httpx2`, `mcp-types` and `starlette` were imported by the application but only arrived as other packages' dependencies; `mcp>=1.2` allowed the 1.x SDK the code no longer works with | Declared in `pyproject.toml` (`mcp>=2.3`); an architecture test checks every third-party import is a declared dependency |
| No warning when `MCP_TOKEN_SECRET` kept the development value | Warning at API start-up, with a test |
| Stale documentation (this file, the "8 cases" cost, the review date of "Criteria not met", an assumption still "to be evaluated") and the 13:13 LLM answers not committed | Corrected; answers in `evals/results/` |

**Detected by the candidate**

| Error | Fix |
| --- | --- |
| The `llm_app` fixture was synchronous and failed with `RuntimeError: no running event loop` when running the model evals. **The traceback printed the full Gemini key** | Async fixture, and `GEMINI_API_KEY` (later also `MCP_TOKEN_SECRET`) as `SecretStr`, with tests checking it does not appear in `repr`, `str` or tracebacks |
| A wrong date (2026-11-18 instead of 2026-11-17) in migration 0004 and in ADR 0003 | Corrected |
| During a Docker test, the agent did not check that `.env` had `GEMINI_API_KEY` and made **an unintended real call to Gemini** (it responded `503`; the app correctly returned `502`) | The agent reported it instead of hiding it. Since then `scripts/e2e.sh` blanks the key |
| The first model chosen for the assistant (Anthropic) was not the one the candidate wanted | ADR 0006, which supersedes that decision |
| The agent built the rehearsal stack and rehearsed the demo **while fault injection was running in the background**, so the image may have included mutated code (`if False:` in `reservations.py`) | Those rehearsals were invalidated. It was checked that the code had been restored and the full suite was run. The stack was rebuilt without cache, verifying that the code in the images was byte-for-byte identical to the repo's, and the rehearsals were repeated. New rule 9 in `CLAUDE.md`: nothing in parallel with mutations |

## What the candidate decided

- **Stack and architecture:** Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL and Alembic; hexagonal modular monolith; mock as a separate service consumed only over HTTP; read-only MCP; Docker Compose for everything.
- **Assistant provider:** first Anthropic and then **Gemini with `google-genai`**, without LangChain or LangGraph. Asked for the current Flash model to be chosen **by verifying it in the documentation, not from memory**.
- **Concurrency:** `400 VERSION_REQUIRED` instead of `428` when the version is missing.
- **Work plan:** day-by-day plan and order of stories, with code frozen on Friday at 18:00.
- **Security:** secrets as `SecretStr`. Gemini retries only for 429, 503 and the sporadic 403, never for 400, 401 or 402; the agent also added 5xx and reported it.
- **Frontend:** React + Vite + TypeScript without a UI library; SSE read with `fetch`; 409 with re-apply; the UI is not the permission barrier; order of blocks with one commit per block.
- **Method:** inject faults in every story, one commit per task, and present nothing as done without a test or a run.
- **Model evaluations:** the candidate ran them with their own key (5 of 5 on 2026-11-17). On 2026-11-18 at 01:08 they ran the 7 cases, including the 2 US-11 cases added that day, and 7 of 7 passed. At 01:31 they ran the gaps case, added based on what they observed in the previous run, and it passed 1 of 1 (14 s). On 2026-11-20 at 13:13 they ran the 9 cases (the preferences case was added that day) 3 times each: 24 of 27 passed; the 3 failures were a too-strict check, which the candidate decided to change after reviewing the answers, and the 2 affected cases then passed 6 of 6 at 13:26. The 27 answers of the 13:13 run are committed in [`evals/results/`](../evals/results/). The agent ran none of these runs; it rechecked the saved 13:13 answers against later checker changes, without model calls ([`evals/README.md`](../evals/README.md)).
- **Testing the assistant from the UI with a real key:** done by the candidate on 2026-11-17 (see `docs/demo.md`, step 7).
- **Gap closing on 2026-11-18:** respect `Retry-After` in reconciliation, propagate `X-Request-Id` to worker jobs, and evaluate with the model the relation to dates and constraints, schedule conflicts and nights without accommodation.

## How the output was verified

- **`uv run pytest`:** backend, mock and deterministic evals; 690 pass (the 9 model ones excluded), 2026-11-20.
- **`npm test`:** 35 Vitest tests pass, 2026-11-20.
- **`scripts/e2e.sh`:** 5 of 5, from scratch, 2026-11-20.
- **Static checks:** `ruff`, `mypy --strict` and strict `tsc`.
- **Fault injection** (`scripts/mutations/`).
- **Docker rehearsals** of the demo script ([`docs/demo.md`](demo.md) states which steps were rehearsed and which were not).
- **Candidate review:** reading the diffs and the ADRs in every delivery.
