# 0008. English UI and docs; backend messages shown as returned

- **Status:** Accepted
- **Date:** 2026-11-18
- **Related stories:** US-01 … US-12 (all UI flows), US-02 (roles and `403`), US-01 and US-06 (validation, `VERSION_REQUIRED`, version `409`)

## Context

- The assignment (`assignment/`) is in English and the review is expected in English, but the repository was written in Spanish: README, docs, ADRs, UI texts, comments and test names.
- The backend API, its error messages, the provider contract and the MCP tools were already in English. The UI mixed both languages: Spanish labels next to English backend messages.
- A previous change (commit `f50d823`) went the other way: Spanish role labels in the UI (`lector` for `viewer`) and Spanish texts for known error codes, with the backend message moved into a collapsible section. That partly superseded decision 4 of ADR 0007 (errors shown as returned).

## Options considered

1. **Keep Spanish and translate backend messages in the UI by `code`** (the `f50d823` approach). The UI reads in one language, but it depends on the wording of free-text messages (the roles in a `403` were parsed from its text), every new code needs a translation, and the repository would not match the language of the assignment.
2. **Translate the backend messages in the backend.** Changes the API contract and the texts used by tests, evaluations and the assistant. Rejected.
3. **Everything in English, backend messages shown as returned.** One language across repository, UI and API, and nothing to keep in sync between backend messages and UI texts.

## Decision

Option 3.

- README, `CLAUDE.md`, `docs/` (including every ADR, with English file names), evaluation and mutation-testing docs, the `/story` command (`.claude/commands/story.md`, formerly `historia.md`), UI texts, code comments and test names are in English.
- The UI shows the roles with their API values: `organizer`, `editor`, `viewer`.
- Backend errors are shown exactly as returned (status, code, message, details and `requestId`), as decision 4 of ADR 0007 states. The translation by code from `f50d823` is removed and ADR 0007 is again in force without exceptions.
- Unchanged: `assignment/`, the provider contract, and the backend logic and messages.
- Intentional exceptions: proper names in the fixtures (e.g. *Belém*), and the Spanish patterns and sample answers in `evals/`. The assistant answers in the user's language, so the checkers must also recognise answers in Spanish.

## Consequences

- Reviewers read the code, the docs and the UI in the language of the assignment, and the UI and API messages match.
- There are no translation tables to maintain, and a new backend error code needs no frontend change.
- Old links to the Spanish ADR file names break. Links inside the repository are updated.
- **Verification:** `frontend/src/components.test.tsx` (`ErrorBox` shows `403 FORBIDDEN` and `422 VALIDATION_ERROR` with the backend message as returned), `frontend/src/tabs/ParticipantsTab.test.tsx` (role selects show and send `editor`/`viewer`), `frontend/e2e/flow.spec.ts` (the viewer's role and the backend's `403` text against Compose), and a grep for accented characters and `ñ` over the repository (excluding `assignment/`). Only the exceptions listed above remain.
