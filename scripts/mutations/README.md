# Fault injection (mutations)

Checks that the tests catch real faults and do not just confirm canned responses. Each mutation removes a check, changes a state or breaks a guarantee. The runner applies it, runs the tests that should catch it and restores the file.

- **Killed:** some test fails.
- **Survives:** everything passes. Either a test is missing, or another layer defends the same thing; in that case there is a mutation that removes both layers.

## Usage (from the repo root)

```bash
docker compose up -d db                               # the backend suites use PostgreSQL
python3 scripts/mutations/run.py --list               # suites and mutations
python3 scripts/mutations/run.py                      # all except ui-e2e
python3 scripts/mutations/run.py bookings secrets     # only some
```

**`ui-e2e` suite.** It needs the UI served by Vite, so that each mutation is reloaded immediately; a Docker image would not reflect the change. Set it up like this:

```bash
KEEP=1 ./scripts/e2e.sh                                               # leaves the tp-e2e stack running
(cd frontend && API_URL=http://localhost:18000 npx vite --port 5174)  # in another terminal
python3 scripts/mutations/run.py ui-e2e                               # E2E_BASE_URL=http://localhost:5174 by default
COMPOSE_PROJECT_NAME=tp-e2e docker compose down -v                    # when done
```

**Runner safety**
- It refuses to mutate a file with uncommitted changes.
- It always restores the original, also on Ctrl+C.
- It exits with code 1 if any mutation survives or no longer matches the code (`PATTERN NOT FOUND`).
- It takes about 15–20 s per backend mutation (about 15 min in total) and under 2 s per Vitest mutation.

## Files

- `run.py`: the runner.
- `suites.py`: the mutations, as exact fragments of the current code. If the code changes, the mutation reports it instead of being silently skipped.

**Origin of the mutations**
- They were copied from the scripts used during development: `mutate.py` (bookings), `mutate_docs.py`, `mutate_mcp.py` and `mutate_ui.py`.
- `secrets` gathers the ones that were done by hand with `sed` (secrets and Gemini retry).
- The scripts of the first stories (provider mock and US-01 to US-06) **were not kept**: they were overwritten outside the repo. Their results are only recorded in the commit messages. On 2026-11-20 the `core` suite rebuilt the US-01 to US-06 part (see below); the provider mock still has none.

## Results of the latest run

Run on 2026-11-18 with this runner, **with nothing else running**. Afterwards it was checked that `git diff -- backend/ frontend/src` was empty and that no `.orig` files were left. The `mcp` and `planning` rows are from a later run on 2026-11-20 (see below).

| Suite | Mutations | Killed | Survivors |
| --- | --- | --- | --- |
| `bookings` | 17 (rerun 2026-11-20) | 17 | — |
| `tracing` | 3 (rerun 2026-11-20) | 3 | — |
| `documents` | 12 (rerun 2026-11-20) | 11 | "in-flight requests not deduplicated" (see below) |
| `mcp` | 28 (2026-11-20) | 28 | — |
| `planning` | 2 (2026-11-20) | 2 | — |
| `rate-limit` | 2 (2026-11-20) | 2 | — |
| `core` | 16 (2026-11-20) | 16 | — |
| `secrets` | 7 (rerun 2026-11-20) | 7 | — |
| `ui-unit` (Vitest) | 11 (rerun 2026-11-20) | 6 | does not reread on reconnect, reapply with the old version, ignores booking events, buttons for all roles, sends terms other than the reviewed ones |
| `ui-e2e` (Playwright) | 11 | 10 | "unknown looks confirmed" |

**Reading the survivors**

- **Documents, "in-flight requests not deduplicated".** It is defence in depth. Without the application check, the partial unique index from migration 0005 (one active document per version) raises `Conflict`, and the `except` branch returns the running document: the behaviour does not change. The mutation that removes **both layers** ("not deduplicated in the app nor on hitting the index") is killed.
- **UI.** The two layers complement each other, and **all 11 mutations are killed by at least one**:
  - the e2e catches the ones in the flow with the real backend: reread on reconnect, reapply, live events, roles and terms sent;
  - Vitest catches "unknown looks confirmed", a state the e2e cannot trigger from the browser.

**Fixes made while running**

- **SSE parser.** The mutation deleted a line and left a dangling `else`, so it was "killed" by a syntax error and not by a test. It was rewritten to be valid, and now 4 Vitest tests and the e2e catch it.
- **"Ignores booking events" in the e2e.** On 2026-11-17 it was killed, but on 2026-11-18 it survived. The test reopened the Bookings tab after confirming the booking at the provider, and that reread with `GET` could hide the missing live event depending on timing. The test was fixed: the tab stays open showing `pending` before the confirmation. Since then it is killed in 2 of 2 runs.

**Mutations added on 2026-11-18** (run with the runner after each commit, with nothing else running):

| Suite | Mutation | Result |
| --- | --- | --- |
| `bookings` | reconciliation ignores Retry-After | Killed (3 tests) |
| `tracing` | the repository does not copy the request id to the job | Killed (2 tests) |
| `tracing` | the worker does not restore the job's id | Killed |
| `tracing` | the middleware does not restore the context | Killed |
| `mcp` | dates checker accepts everything | Killed (5 tests) |
| `mcp` | restrictions checker accepts everything | Killed (7 tests) |
| `mcp` | conflict without naming the items | Killed (2 tests) |
| `mcp` | changes checker never fires | Killed (4 tests) |
| `mcp` | every offer counts as proposed | Killed (2 tests) |
| `mcp` | gap not tied to the nights | Killed (2 tests) |
| `mcp` | gap without a word of absence | Killed (2 tests) |
| `mcp` | absence and accommodation in different sentences | Killed (1 test). It survived the first manual try and was closed with a new negative case before commit `c0293c7` |
| `mcp` | gaps without checking changes | Killed (1 test) |

The last 4 belong to the gaps checker and were run with the runner after commit `c0293c7`.

**Mutations added on 2026-11-20** (new `planning` suite, run alone with `python3 scripts/mutations/run.py planning` once the code was committed, with nothing else running; the tree was clean afterwards):

| Suite | Mutation | Result |
| --- | --- | --- |
| `planning` | preferences length not validated | Killed (3 tests). The database check (`char_length(preferences) <= 500`) is a second layer: without the domain check the write fails, but not as a per-field `422` |
| `planning` | missing transport never reported | Killed (4 tests: unit, integration and MCP contract) |

**Run of 2026-11-20 on a clean clone of `8d25f59`** (another machine, reported by the project owner): `python3 scripts/mutations/run.py mcp planning`, **30 of 30 killed** (`mcp` 28 of 28, `planning` 2 of 2). That run covers the changes made since 2026-11-18 to the `mcp` suite:
- "instructions without the data-not-instructions rule", updated to the new wording of that rule (it now also names preferences);
- "booking checker never fires (active voice)", "conflict without naming the items", "gap without a word of absence" and "gap as 'no' and a lodging word in one sentence (the 2026-11-20 false positive)", updated to the fixed checkers (the last one replaces "absence and accommodation in different sentences");
- three new ones for the checker fixes: "negated conflict counts as a conflict", "booking checker ignores the passive voice" and "negated passive counts as a booking claim".

**Run of 2026-11-20 on `5069c99`** (another machine, reported by the project owner): `bookings` and `tracing`, **20 of 20 killed** (`bookings` 17 of 17, `tracing` 3 of 3).

**Run of 2026-11-20 on `f1a62a1`** (another machine, reported by the project owner): `documents` 11 of 12, `secrets` 7 of 7 and `ui-unit` 6 of 11, the same results as on 2026-11-18. The survivors are read as explained above ("Reading the survivors").

`ui-e2e` was not rerun on 2026-11-20; its result (10 of 11) is from 2026-11-18.

**Shared provider rate limit, 2026-11-20** (new `rate-limit` suite, run alone with `python3 scripts/mutations/run.py rate-limit` once the code was committed, with nothing else running; the tree was clean afterwards):

| Suite | Mutation | Result |
| --- | --- | --- |
| `rate-limit` | a shorter 429 shortens the stored wait (no GREATEST) | Killed (3 tests) |
| `rate-limit` | the provider is called during the shared rate limit | Killed (4 tests) |

**Core suite, 2026-11-20** (US-01 to US-06, run alone with `python3 scripts/mutations/run.py core` once committed, with nothing else running; the tree was clean afterwards). **16 of 16 killed.** The runner now also accepts mutations that change several files at once, used here to remove both layers of the trip-edit check.

| Mutation | Tests that failed |
| --- | --- |
| role never checked in `TripAccess.require` | 18 |
| non-participant gets in as a viewer in `load_access` | 13 |
| removing a participant does nothing | 1 |
| no version check in `_start` | 9 |
| `bump_version` without `WHERE version` | 9 |
| trip update without `WHERE version` | 2 |
| trip update without `WHERE version` and without the `_start` check | 15 |
| end before start accepted | 3 |
| partial search reported as complete | 4 |
| `_overlaps` always `False` | 4 |
| `_overlaps` with `<=` | 1 |
| expired price never flagged | 2 |
| outside trip dates never flagged | 2 |
| confirmed cost uses the current price | 4 |
| half-even rounding | 1 |
| budget percentage rounded down | 5 |

Several are killed by only 1 or 2 tests ("removing a participant does nothing", "`_overlaps` with `<=`", "half-even rounding"): they are detected, but by a single case each.

**Total:** 98 distinct mutations (62 + 13 + 2 `planning` + 3 new `mcp` + 2 `rate-limit` + 16 `core`). Only one is not caught by any test layer, the documents defence-in-depth one, and removing both of its layers is caught.
