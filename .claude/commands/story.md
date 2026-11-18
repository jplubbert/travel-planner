---
description: Implement a PRD user story with tests, assumptions, an ADR if warranted, fault injection and a commit
argument-hint: US-XX [notes]
---

Implement $ARGUMENTS following `CLAUDE.md`.

1. Reread the story's section in `assignment/prd.md` and the relevant parts of `assignment/openAPI.json` and `assignment/candidate-guide.md`. Quote the acceptance criteria you are going to cover. Do not modify anything in `assignment/`.
2. Respect the boundaries: rules in `backend/app/domain` (no frameworks), ports in `ports/` and adapters in `adapters/`. The API, the MCP server and the worker only orchestrate.
3. Every criterion needs an automated test that proves it against real Postgres and the mock over HTTP where applicable. Provider failures are triggered with `/_control` only from tests. If something cannot be tested, say so and document why.
4. Every ambiguity in the PRD or the contract goes to `docs/assumptions.md`. Decisions with real alternatives go to a new ADR in `docs/adr/`, added to the index.
5. Inject faults: add the relevant mutations to `scripts/mutations/suites.py`, run them (`python3 scripts/mutations/run.py <suite>`) and close the surviving ones with tests. If one survives because of defence in depth, explain it.
6. Pass `uv run ruff check .`, `uv run mypy backend/app` and `uv run pytest` (and `npm test` in `frontend/` if you touched the UI).
7. Update the status in `README.md` without presenting anything without a test as done, and commit.

When finished, report: criteria covered and their tests, new assumptions, mutations detected and surviving, and what remains pending.
