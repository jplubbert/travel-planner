# tests — Backend tests

- `unit/test_provider_http.py`: mapping of each status code and transport failure to its domain error, overall deadline, logs with `requestId` and without the token.
- `unit/`: pure domain (roles, booking state machine, documents, jobs, money) and architecture rules (the core does not import frameworks; the app never calls `/_control`). They need no services.
- `integration/`: API and repositories against **real PostgreSQL**. The session recreates the `TEST_DATABASE_URL` database (by default `travel_test`) and migrates it with Alembic (upgrade, full downgrade and upgrade again); each test starts with empty data tables. Requires `docker compose up -d db`.
  - `test_us01_trips.py`, `test_us02_sharing.py`, `test_us03_search.py`, `test_us04_itinerary.py`, `test_us05_costs.py`, `test_us06_concurrency.py`, `test_us07_us08_bookings.py`, `test_us09_us10_documents.py`, `test_status_channel.py`: acceptance tests per story. The SSE channel is tested with a real uvicorn inside the test, because `ASGITransport` does not stream infinite responses.
  - `test_worker_and_integrity.py`: the job queue (two workers never claim the same job, claiming by expired lease, deduplication) and booking integrity in the database (immutability trigger, one active attempt per item).
  - The US-07/08 failures the mock cannot trigger on its own (process that dies before or after sending, terms that change just before the `POST`) are simulated by wrapping the real HTTP client (`WrappedProvider`).
  - The provider mock is started **as a real HTTP process** (uvicorn on a free port) once per session. Failure scenarios are enabled with its `/_control` **only from tests** (fixture `mock_control`, which also resets the mock).
  - `test_provider_client.py`: each operation of the `ProviderClient` port against the real mock, including the lost response.
  - `test_persistence.py`: migrations == models, rollback of uncommitted work, database constraints, round-trip of each repository.

The provider mock's tests are in `mock/tests/`.

The MCP and assistant evaluations are in `evals/` (see `evals/README.md`): the deterministic ones are part of the default run and the model ones are marked `llm`.

The frontend tests (Vitest and Playwright) are in `frontend/` (see `frontend/README.md`).
