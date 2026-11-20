# Deployment proposal

This is a proposal: **it has not been deployed**. The only thing that has been run is Docker Compose locally (`docker compose up --build` and `scripts/e2e.sh`).

## Services

The same images as in Compose:

| Service | Image | Replicas | Notes |
| --- | --- | --- | --- |
| `frontend` | `frontend/Dockerfile` (static build + nginx) | 2+ behind the load balancer | Stateless. In production the bundle could be served from a CDN, but the `/api` proxy (same origin) must be kept, or CORS configured |
| `api` | `backend/Dockerfile` | 2+ | No in-memory state: all state is in PostgreSQL and storage. Health: `/healthz` (liveness) and `/readyz` (database reachable) |
| `worker` | `backend/Dockerfile`, `python -m app.workers` | 1+ (scalable) | See "Worker scaling" |
| migrations | `backend/Dockerfile`, `alembic upgrade head` | job before each deployment | Today the `api` container runs them on startup. With several replicas a single job beforehand is preferable (see below) |
| provider | the real one, not the mock | — | `PROVIDER_BASE_URL` and `PROVIDER_TOKEN`. The mock is not deployed to production |

**Deployment order**

1. Migrations job.
2. `api`, with a *rolling update* that waits for `/readyz`.
3. `worker`.
4. `frontend`.

Migrations must be compatible with the previous version during the *rolling update*: add first, and only drop in a later deployment.

**Graceful shutdown**

- **Worker:** on `SIGTERM` it finishes the current job and exits (`app/workers/__main__.py`). If it dies first, the lease expires (`WORKER_JOB_LEASE_SECONDS`) and another worker picks up the job.
- **API:** on shutdown, the lifespan stops the MCP session manager, closes the provider HTTP client and releases the connection pool. In-flight requests are drained by uvicorn. If a booking is left halfway, it stays in `submitted` and the worker reconciles it (rehearsed in the demo).

## Database

- Managed PostgreSQL (16+), with automatic backups and point-in-time recovery.
- Connection pool sized per replica. Each open SSE stream uses connections when polling, so it counts too.
- Retention: the history of bookings, documents and `trip_changes` is not deleted. An archiving policy is needed if it grows.

## Document storage

- **Today:** a local volume (`DOCUMENT_STORAGE_DIR`) shared by the API and the worker, with atomic writes (temp file, `fsync` and `rename`). This requires both to mount the same volume, which does not scale across machines.
- **Production:** an S3 (or compatible) adapter for the `DocumentStorage` port (`app/ports/documents.py`). With it:
  - the object key is still `trip/document.html`;
  - uploading a complete object is already atomic, so no temp file or `rename` is needed;
  - the sha256 is still verified on every download;
  - the orphan sweep lists the bucket;
  - the download can keep going through the API, which checks permissions and hash, or use short-lived presigned URLs issued **after** checking permissions and hash.
- The domain does not change: only the adapter.

## Secrets and configuration

All configuration is read from the environment (`backend/app/config.py`, documented in `.env.example`).

- Secrets are injected from a secrets manager (for example, the chosen cloud's or Vault), never from the repository:
  - `DATABASE_URL`;
  - `PROVIDER_TOKEN`;
  - `MCP_TOKEN_SECRET`: must be changed, because the default value is for development only;
  - `GEMINI_API_KEY`.
- `GEMINI_API_KEY` and `MCP_TOKEN_SECRET` are `SecretStr`: they do not appear in `repr`, logs or tracebacks.
- `MCP_TOKEN_SECRET` must be rotated without cutting sessions. Tokens last `MCP_TOKEN_TTL_SECONDS` (300 s), so accepting two secrets during a window is enough. **Not implemented:** today there is a single secret.

## Worker scaling

- Jobs are claimed with `FOR UPDATE SKIP LOCKED` and a lease, so several replicas do not step on each other (`tests/integration/test_worker_and_integrity.py` tests this with two workers). Scale by queue depth: `queued` jobs with `run_after <= now()`.
- Deduplication (unique key only among active jobs) prevents enqueuing the same reconciliation or generation twice.
- The provider's rate limits are global. More workers does not mean more reconciliations per second if the provider responds `429`.
- Reconciliation uses exponential backoff (`BOOKING_RETRY_BASE_SECONDS` up to `BOOKING_RETRY_MAX_SECONDS`), but **never retries before the `Retry-After`** of a provider `429`, even if it exceeds the backoff maximum (since 2026-11-18, `retry_delay` in `app/domain/reservations.py`, with tests).
- Since 2026-11-20 the `Retry-After` is shared between replicas: it is stored in PostgreSQL (`provider_rate_limits`), and no API or worker process calls the provider before it (ADR 0003, note of 2026-11-20). There is no proactive quota (requests per second) on our side: the limit only starts with the provider's first `429`.

## Observability

**What exists**

- Every request carries `X-Request-Id`, which is accepted or generated, appears in the logs and is returned in errors (`requestId`). Jobs created by the request inherit it, and the worker uses it in its logs and in provider calls.
- Every provider call logs the operation, status, duration, the provider's `requestId` and the request's, never the token.
- Bookings store `lastError` (code, message, `providerRequestId`, `retryAfterSeconds`, time) and documents store `error`.
- The worker logs each job with its outcome and attempt.

**What to add**

- JSON logs to the platform's aggregator.
- Metrics:
  - latency and errors per endpoint and per provider operation;
  - depth and age of the job queue;
  - bookings in `unknown` or `submitted` beyond their deadline;
  - `failed` documents;
  - open SSE streams;
  - model calls and tokens consumed.
- Alerts:
  - bookings unresolved for more than N minutes;
  - growing job queue;
  - `5xx` rate;
  - `/readyz` failing.
- Traces (OpenTelemetry) from the API to the provider and the worker.
  - What already exists: the request's `X-Request-Id` is stored in the job (`jobs.request_id`) and the worker restores it while processing, so its logs and the provider calls it makes carry the same id.
  - Jobs created by the worker sweep have no originating request, so they carry no id; they are traced by `clientReference`.

## What would change for production

| Today | Production | Reason |
| --- | --- | --- |
| Test identity in `X-User-Id` | OIDC (for example, Auth0, Cognito or Keycloak): the API validates the JWT and gets the user from `sub`. `load_access` does not change | Anyone can send a header; the PRD allows test identities only for the submission |
| MCP tokens issued only for the assistant | If there are external MCP clients, an authenticated endpoint that issues short-lived tokens (or OAuth per the MCP specification) | Not needed today (ADR 0006) |
| Documents in a local volume | S3 or compatible, through the `DocumentStorage` port | Multiple machines, durability, cost |
| SSE polling PostgreSQL every second per stream | `LISTEN/NOTIFY`: use cases emit `NOTIFY trip_<id>` on commit, and each replica keeps one `LISTEN` connection and fans notifications out to its streams. The `ready` event and re-read with `GET` are kept, plus a slow poll as a fallback | Polling scales linearly with connections; notifications remain only hints |
| Migrations on API startup | Single job before deployment | Avoids races between replicas |
| Assistant in the API process | Keep it, with per-user concurrency limits, or split it out if its load competes with the API | Today it shares the connection pool (ADR 0006) |
| Provider mock | Real provider | The mock exists only for development, tests and the demo |

## Cost

Not estimated, beyond the model: model evaluations cost about 0.16–0.32 USD per run of the 8 cases, an unmeasured estimate (`evals/README.md`).
