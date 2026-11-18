# Architecture Decision Records (ADR)

This directory records the project's relevant architecture decisions. An ADR captures **why** something was chosen, which alternatives were discarded and which consequences we accept, so the decision can be defended or revisited later.

## When to write an ADR

- The decision affects more than one module, a contract (API, MCP, database) or a guarantee from the PRD.
- There were reasonable alternatives and choosing one has costs.
- Changing the decision later would be expensive.

Local implementation details do not need an ADR.

## Format

File: `NNNN-title-in-kebab-case.md`, with a sequential `NNNN` (`0001`, `0002`, …) and the title in English kebab-case. Numbers are never reused.

```markdown
# NNNN. Title

- **Status:** Proposed | Accepted | Superseded by NNNN | Rejected
- **Date:** YYYY-MM-DD
- **Related stories:** US-XX

## Context
Problem, forces at play and requirements (quote the PRD / contract).

## Options considered
Each alternative with pros and cons.

## Decision
What is chosen and why.

## Consequences
Positive, negative, risks, and what has to be done/tested as a result.
```

## Rules

- An accepted ADR is immutable except for minor corrections. To change the decision, write a new ADR and mark the previous one as "Superseded by NNNN".
- Every ADR must state how it is verified (tests that prove the guarantee).

## Index

| No. | Title | Status |
| --- | --- | --- |
| [0001](0001-version-based-concurrency.md) | Trip edit concurrency through optimistic versioning | Accepted |
| [0002](0002-provider-mock-with-control.md) | Provider mock with base fixtures, out-of-contract controls and a controllable clock | Accepted |
| [0003](0003-booking-states-and-reconciliation.md) | Local booking states and reconciliation | Accepted |
| [0004](0004-offer-snapshot-and-freshness.md) | Offer snapshot in the item and freshness policy | Accepted |
| [0005](0005-jobs-documents-and-status-channel.md) | Jobs in PostgreSQL, document as an immutable snapshot, and status channel | Accepted |
| [0006](0006-mcp-assistant-and-model-provider.md) | In-process MCP, session-based identity, custom loop and swappable model provider | Accepted |
| [0007](0007-frontend-same-origin-and-live-state.md) | Same-origin frontend, SSE read with fetch, and a UI that is not the permission barrier | Accepted |
| [0008](0008-english-ui-and-docs-backend-messages-as-is.md) | English UI and docs; backend messages shown as returned | Accepted |
