---
description: Implementar una historia del PRD con tests, supuestos, ADR si procede, inyección de errores y commit
argument-hint: US-XX [notas]
---

Implementa $ARGUMENTS siguiendo `CLAUDE.md`.

1. Relee la sección de la historia en `assignment/prd.md` y las partes relevantes de `assignment/openAPI.json` y `assignment/candidate-guide.md`. Cita los criterios de aceptación que vas a cubrir. No modifiques nada en `assignment/`.
2. Respeta las fronteras: reglas en `backend/app/domain` (sin frameworks), puertos en `ports/` y adaptadores en `adapters/`. La API, el MCP y el worker solo orquestan.
3. Cada criterio necesita un test automatizado que lo pruebe contra Postgres real y el mock por HTTP cuando aplique. Los fallos del proveedor se provocan con `/_control` solo desde tests. Si algo no se puede probar, dilo y documenta por qué.
4. Cada ambigüedad del PRD o del contrato va a `docs/assumptions.md`. Las decisiones con alternativas reales van a un ADR nuevo en `docs/adr/`, añadido al índice.
5. Inyecta errores: añade las mutaciones relevantes a `scripts/mutations/suites.py`, ejecútalas (`python3 scripts/mutations/run.py <suite>`) y cierra con tests las que sobrevivan. Si una sobrevive por defensa en profundidad, explícalo.
6. Pasa `uv run ruff check .`, `uv run mypy backend/app` y `uv run pytest` (y `npm test` en `frontend/` si tocaste la UI).
7. Actualiza el estado en `README.md` sin presentar como hecho nada sin test, y haz commit.

Al terminar, informa: criterios cubiertos y sus tests, supuestos nuevos, mutaciones detectadas y supervivientes, y lo que queda pendiente.
