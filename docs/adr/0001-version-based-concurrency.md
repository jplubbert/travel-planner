# 0001. Trip edit concurrency through optimistic versioning

- **Status:** Accepted
- **Date:** 2026-11-17
- **Related stories:** US-06 (primary), US-02, US-04

## Context

US-06 — *Edit without silently losing changes* requires:

- Concurrent edits do not silently discard changes that were already saved.
- Each participant can tell whether their change was saved.
- Whoever runs into a conflict understands what happened and can resolve it.
- Conflict handling preserves permissions and trip consistency.
- Live presence and real-time collaborative editing are **optional**; avoiding loss is **mandatory**.

Several editors can modify the same trip (trip data and itinerary) from different sessions, with tabs left open for minutes. The client works on a copy that can become stale. Bookings (US-08) and documents (US-09) have their own idempotency and state mechanisms and are not part of this decision.

## Options considered

### A. Pessimistic locking (edit lock)

The editor "takes" the trip (a DB lock or a `locked_by` row with a TTL) and nobody else can edit until it is released or expires.

- ✅ Write conflicts are impossible while the lock is held.
- ❌ A lock held across HTTP requests needs a TTL, renewal and release: if the user closes the tab, everyone else is blocked until it expires.
- ❌ Poor experience for groups: it blocks even edits that do not overlap.
- ❌ A `SELECT … FOR UPDATE` only works within a transaction; it does not protect the user's "read → think → save" window, which is where the loss happens.

### B. Automatic merging (per-field last-write-wins, CRDT/OT)

The server merges concurrent changes automatically.

- ✅ No interruptions for the user in the common case.
- ❌ Last-write-wins **is** silent loss, exactly what US-06 forbids.
- ❌ CRDT/OT is much more complex, designed for real-time collaboration (optional in the PRD), and can still produce semantically inconsistent itineraries (two overlapping activities, budget exceeded) without anyone deciding it.
- ❌ Disproportionate cost for the deadline.

### C. Optimistic version control with 409

Each trip has an integer `version`. Every read returns it; every write must send the version it was based on. The server applies the write only if it matches and increments it; otherwise it responds `409 Conflict`.

- ✅ No saved change is overwritten without its author having seen the current version.
- ✅ No session state or locks to expire; works with disconnections and retries.
- ✅ Simple to implement atomically (`UPDATE … SET version = version + 1 WHERE id = :id AND version = :expected`) and to test with real concurrent requests.
- ✅ The outcome is explicit: success with a new version, or 409 with the current state.
- ❌ The user who loses the race has to reapply their change.
- ❌ With one version per trip there are "false conflicts" between edits that do not overlap (e.g. two different items).

## Decision

**Option C: optimistic control with a version at the `Trip` aggregate level.**

- `Trip` (metadata + itinerary) is an aggregate with a single `version: int`.
- Read responses include `version` (body and `ETag` header).
- Every mutation requires the expected version (`version` field in the `PATCH` body; reads also expose it in `ETag`. Minor correction of 2026-11-17 during implementation). Missing version → `400` with code `VERSION_REQUIRED` and a message explaining that the version that was read must be sent; nothing is applied. (`428 Precondition Required` was discarded: it is semantically correct but little known, and the goal is for errors to be understandable.)
- The check and the increment happen in the **same statement/transaction** as the write, in the persistence adapter; the domain expresses the rule through the repository port (`save(trip, expected_version)` → success or `VersionConflict`).
- On conflict: `409` with code `TRIP_VERSION_CONFLICT`, the current version and the current state of the trip (and, when possible, who changed it and when), so the client can show the difference and the user can reapply or discard.
- Authorization is evaluated **before** checking the version and again on retry: a conflict never reveals data to someone without access, nor lets a viewer write.
- Itinerary consistency validations run on the resulting state on every accepted write.

The coarse granularity (one version per trip) is accepted for simplicity; if false conflicts become annoying, it can evolve to per-item versions in a later ADR without changing the basic contract (409 + current state).

## Consequences

**Positive**

- Meets US-06: there is no silent loss; the user knows whether they saved (2xx with a new version) or not (409).
- No orphaned locks and no dependency on persistent connections.
- Compatible with recovery after a disconnection: the client can always reread the current state and version.

**Negative / risks**

- More friction when two editors work on the same trip at the same time.
- The frontend must handle the 409 (show the current state, allow reapplying).
- Every write path must go through the same mechanism; a path that bypasses it would break the guarantee.

**Implementation note (2026-11-17, US-04/US-06).** All trip and itinerary mutations share this version, and each accepted version leaves an entry in `trip_changes`, which the `409` returns in `changesSinceYourVersion` together with the current state. The version check reads the trip with `SELECT … FOR UPDATE`. Under READ COMMITTED, without that lock, a later validation read could see a commit newer than the checked version and respond `422` instead of `409`. An intermittent concurrency test uncovered it, and `test_validation_reads_the_same_version_it_checked` now reproduces it deterministically. Operations that call the provider release the lock before the network call; the final compare-and-set still guarantees that nothing is overwritten.

**Verification (required tests)**

- Two concurrent updates with the same base version: exactly one succeeds, the other gets 409, and the saved change is still present.
- Write without a version → 400 `VERSION_REQUIRED` and no changes.
- A viewer or non-participant gets 403/404 before any 409, and the 409 leaks no data.
- The version is incremented exactly once per accepted write.
