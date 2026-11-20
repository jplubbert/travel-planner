# 0005. Jobs in PostgreSQL, document as an immutable snapshot, and status channel

- **Status:** Accepted
- **Date:** 2026-11-17
- **Related stories:** US-08, US-09, US-10, US-02 (subscriptions), and recovery after disconnection from the "Cross-cutting behavior" section

## Context

- **US-09:** generate the document without blocking the user, with visible states (`waiting`, `in progress`, `complete`, `failed`) that are recovered on reload or reconnect. Neither interruptions nor repeated requests may leave a missing or incomplete file marked as ready, and there must be a way to retry.
- **US-10:** the document identifies which information and which moment it represents; later edits do not alter it; only participants can download it. Mismatches between files and references must be detected or corrected.
- **US-08 (ADR 0003):** needs resumable background work: reconciliation and polling of pending bookings.
- **Cross-cutting:** recover the state after a disconnection without relying on every notification arriving. Permissions also apply to status subscriptions (US-02).

Three things need deciding: where background jobs live, what a document is, and how the client learns about changes.

## Decision 1: jobs live in PostgreSQL (`jobs` table)

**Options considered**

- **In-process in-memory tasks (`asyncio.create_task`, `BackgroundTasks`).** Lost on restart: a pending reconciliation or generation vanishes without a trace. Discarded.
- **External broker (Redis + RQ/Celery/arq).** Workable, but adds a service and a second state store. The job and the business change (creating the document, moving a booking to `unknown`) cannot be committed in the same transaction, and the "I saved the document but did not enqueue the job" window appears. There is no volume that justifies it.
- **`jobs` table in the same database (chosen).**

**How it works**

- The job is inserted **in the same transaction** as the change that motivates it, so one cannot exist without the other.
- Workers claim jobs with `SELECT … FOR UPDATE SKIP LOCKED` and a lease (`locked_until`). Several workers can run at once without processing the same job, and a job whose worker died becomes available again when its lease expires.
- `dedupe_key` is unique only among active jobs, which prevents duplicates without blocking future work.
- **Behaviour per type:**
  - `generate_document`: retries with backoff and, after `DOCUMENT_MAX_ATTEMPTS`, the document becomes `failed`; it can be retried explicitly.
  - `sync_booking`: never gives up (ADR 0003).
- **Cost:** periodic polling of the table (`WORKER_POLL_INTERVAL_SECONDS`) and load on the main database. Acceptable at this scale; a future move to `LISTEN/NOTIFY` or a broker would not change the jobs contract.

## Decision 2: a document is an immutable snapshot, rendered afterwards

**Options considered**

- **Render on the spot from live data.** Blocks the request and, if generated in the background from live data, the document reflects what existed *when the worker got to it*, not when it was requested. Also, regenerating it after losing the file would produce different content. Discarded.
- **Store only the file, without a copy of the data.** If the file is lost or corrupted, there is nothing to regenerate it faithfully from. Discarded.
- **Store the file inside the database (`bytea`).** Consistency is trivial, but it loads the database with binaries and mixes responsibilities. A local disk volume works just as well behind the `DocumentStorage` port.
- **Immutable snapshot in the document row, rendered to a file afterwards (chosen).**

**How it works**

- **On request (`POST /trips/{id}/documents`, editor, `202`):** in one transaction, with the trip row locked, the snapshot is taken (`snapshot`: trip, itinerary with its flags, costs and booking states) together with the trip version and the time. In that same transaction the document is created in `waiting` along with its job.
- **Repeated requests:**
  - While there is a document in progress for the same version, that one is returned. A partial unique index guarantees it: two simultaneous requests share a document.
  - If there is already a `complete` one with the same content (`snapshot_sha256`, excluding the time), it is reused.
- **The worker:**
  1. moves the document to `in_progress`;
  2. renders it **from the snapshot** to self-contained, deterministic HTML;
  3. writes it to a temporary file, calls `fsync`, renames it atomically and calls `fsync` on the directory;
  4. **only then** marks it `complete` with its sha256 and size.

  If it is interrupted at any point, the document stays `waiting` or `in_progress`, never `complete`, and the lease hands the job to another worker.
- **Immutability:** a trigger prevents modifying the snapshot, the version, the trip and the requester. Later edits produce new documents.
- **Download (any participant):**
  - sha256 and size are verified before serving;
  - if the file is missing or does not match, **nothing is served**. For an editor, the problem is recorded and the document goes back to `waiting` to be regenerated from the snapshot; since rendering is deterministic, the same hash comes out. A viewer's download changes nothing (see the note of 2026-11-20);
  - a periodic sweep deletes unreferenced files and old temporary files, and re-enqueues complete documents whose file disappeared or no longer matches its checksum.

## Decision 3: SSE status channel, as a notification only

**Options considered**

- **Client-side periodic polling only.** The most robust, but it gives latency or unnecessary load, and a worse experience for states that change on their own (`pending` bookings, documents).
- **WebSockets.** Bidirectional, which is not needed here (the client sends nothing over the channel). Requires handling our own protocol, reconnection and authentication, and adds nothing over SSE.
- **SSE with Postgres `LISTEN/NOTIFY`.** Fewer queries, but notifications are lost if nobody is listening, and a dedicated connection per process must be maintained. A possible later optimisation.
- **Per-trip SSE, with server-side polling (chosen).**

**How it works**

- `GET /trips/{id}/events` (any participant; a non-participant gets `404`). Every `EVENTS_POLL_SECONDS`, the stream compares a fingerprint of the trip state (version, plus state and `updated_at` of bookings and documents) and emits `trip`, `booking` or `document` with the id and the new state. It sends `keep-alive` and `retry: 3000`.
- **Access is rechecked on every poll:** if the user stops being a participant, they receive `access-revoked` and the stream is closed.
- **Events are notifications, not the source of truth.** There is no event replay: on (re)connect `ready` arrives and the client **always** rereads with `GET`. A lost or duplicated event cannot leave the client with an incorrect state.

**Note (2026-11-18): job traceability.**
- Each job stores the `X-Request-Id` of the request that created it (`jobs.request_id`, migration 0006). The repository copies it from the request context, so the domain knows nothing about correlation.
- The worker restores that id in the context while processing the job. That way its logs and the provider calls made by the job carry it.
- Jobs created by the worker's sweep have no originating request and get no id.
- The middleware restores the context at the end of each request, so the id does not leak into whatever runs afterwards.
- Test: `tests/integration/test_request_id_propagation.py`.

**Note (2026-11-20): a viewer's download never writes.**
- **Problem:** the download endpoint is open to viewers, and on a damaged file it moved the document to `waiting` and enqueued its regeneration. Viewers are read-only (US-02), so a viewer's request was changing state and creating work.
- **Options considered:**
  - *Keep it and justify it as system recovery, not a user action:* the viewer does not choose anything, and the result is the same document with the same hash. But it still makes a read endpoint write for a read-only role, and the permission model becomes "read-only, except for side effects".
  - *A viewer's download records nothing and enqueues nothing (chosen).* It answers `409 DOCUMENT_NEEDS_REGENERATION` ("The document needs regeneration by an editor…"), with the `problem` (`FILE_MISSING` or `FILE_CORRUPT`) and the unchanged `status`. An editor's download keeps triggering the regeneration (`409 DOCUMENT_FILE_UNAVAILABLE`, document back to `waiting`).
- **Consequence:** without an editor, the repair comes from the worker's storage sweep (`DOCUMENT_SWEEP_INTERVAL_SECONDS`, 300 s by default). For that, the sweep now verifies sha256 and size of every complete document, not only that the file exists: it reads every stored file on each sweep, which is acceptable at this scale (small HTML files); with many documents it would need sampling or a cheaper integrity check.
- **Tests:** `test_a_viewer_hitting_a_damaged_file_changes_nothing_and_the_sweep_repairs_it` (deleted and corrupted file: viewer gets `409`, the document is unchanged and no job is queued; the sweep requeues it and the worker restores the same bytes) and `test_damaged_file_is_never_served_and_is_regenerated_from_the_snapshot` (editor path).

## Consequences

- **Positive:**
  - a single transactional store for business state and jobs;
  - reproducible, verifiable documents;
  - nothing is presented as ready without a verified file;
  - the status channel enforces the same permissions as the rest of the API.
- **Negative / risks:**
  - polling of the `jobs` table and one poll per open SSE stream, which is load linear in the number of connected clients;
  - local disk requires a volume shared between `api` and `worker`, and a multi-machine deployment would need a different adapter (S3 object or similar) behind the same port;
  - if the renderer changes, regenerating an old document produces a different hash; the snapshot remains the source of truth for the content.

## Verification

- `tests/integration/test_us09_us10_documents.py`:
  - immediate `202` and a worker that completes the document;
  - content (itinerary, costs, booking states, version and time);
  - repeated or simultaneous requests that share a document;
  - worker killed while writing or right after writing, recovered via lease;
  - snapshot taken at request time, not at generation time;
  - later edits that do not alter the document;
  - deleted, corrupted or truncated file that is not served and is regenerated with the same hash;
  - a viewer's download of a damaged file changes nothing; the sweep repairs it;
  - orphan sweep;
  - permissions.
- `tests/integration/test_status_channel.py`: document, booking and trip notifications; reconnection without replay plus `GET`; closing on loss of access; `404` and `401`.
- `tests/integration/test_worker_and_integrity.py`: job distribution among workers, lease and deduplication.
- `tests/unit/test_local_storage.py`: a failure mid-write leaves the previous file intact.
