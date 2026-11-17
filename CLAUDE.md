# CLAUDE.md

Instrucciones para agentes de IA (Claude Code u otros) que trabajen en este repositorio.

## Resumen del proyecto

Take-home: plataforma de planificación de viajes en grupo. Un organizador crea un viaje (destinos, fechas, presupuesto en USD), lo comparte con editores y viewers, se buscan opciones en un proveedor externo (transporte, alojamiento, actividades), se arma un itinerario, se hacen reservas simuladas, se generan documentos descargables y un asistente de IA **de solo lectura** recomienda opciones usando herramientas expuestas por MCP.

Fuentes de verdad (en `assignment/`, **nunca modificar**):

- `assignment/candidate-guide.md` — restricciones, criterios de evaluación, entregables.
- `assignment/prd.md` — historias de usuario US-01 … US-12 y criterios de aceptación.
- `assignment/openAPI.json` — contrato del proveedor externo. Se mockea y se consume solo por HTTP.

Plazo: **viernes 20 nov 2026, 23:59**.

## Decisiones ya tomadas

- **Backend:** Python 3.12, FastAPI, SQLAlchemy (async), PostgreSQL, Alembic, httpx, pytest.
- **Arquitectura:** monolito modular con puertos y adaptadores (hexagonal).
  - `backend/app/domain/` es el núcleo: **no importa FastAPI, SQLAlchemy ni httpx** (ni ningún otro framework de I/O).
  - `backend/app/ports/` define interfaces en Python (`typing.Protocol` o ABC) que el dominio necesita.
  - `backend/app/adapters/` implementa esos puertos (Postgres, cliente HTTP del proveedor, almacenamiento de archivos, reloj…).
  - `backend/app/api/`, `backend/app/mcp/` y `backend/app/workers/` son puntos de entrada que orquestan casos de uso; no contienen reglas de negocio.
- **Mock del proveedor:** servicio separado en `mock/` (FastAPI). La aplicación lo consume **solo por HTTP** según `openAPI.json`; nunca su base de datos ni sus endpoints de control.
- **MCP:** servidor con el SDK oficial de Python (FastMCP), **solo lectura**. No expone modificación de itinerario ni reservas. Los permisos se evalúan con la identidad del usuario que actúa, no solo con el `trip_id`.
- **Asistente:** usa la API de Anthropic directamente con el SDK oficial de Python (`anthropic`), modelo `claude-sonnet-4-5`. El bucle de tool use es propio: el asistente actúa como cliente MCP, descubre las herramientas del servidor MCP y las pasa al modelo. **Sin LangChain ni LangGraph** ni otros frameworks de agentes. Modelo y API key se configuran por entorno (`ANTHROPIC_MODEL`, `ANTHROPIC_API_KEY`).
- **Frontend:** mínimo; se define más adelante.
- **Infraestructura:** Docker Compose para todo.

## Reglas de trabajo

1. **Leer el PRD antes de cada historia.** Antes de empezar una US, releer su sección en `assignment/prd.md` (y las partes relevantes de `openAPI.json` y del candidate guide). Citar los criterios de aceptación que se van a cubrir.
2. **Nada está completo sin un test que lo pruebe.** No marcar una historia, criterio o tarea como hecha si no hay un test automatizado que verifique el comportamiento afirmado. Si algo no se puede probar, decirlo explícitamente y documentar por qué.
3. **Supuestos en `docs/assumptions.md`.** Toda ambigüedad del PRD o del contrato y cómo se resolvió va ahí, con la historia o sección a la que afecta.
4. **Decisiones de arquitectura en `docs/adr/`.** Cada decisión relevante (alternativas reales, trade-offs) se registra como ADR numerado según `docs/adr/README.md`. No cambiar una decisión aceptada sin un ADR nuevo que la sustituya.
5. **No modificar `assignment/`.** Ni formato, ni espacios, ni el contrato OpenAPI.
6. **Respetar las fronteras de la arquitectura.** Si el dominio necesita algo externo, se define un puerto. Nunca importar adaptadores desde el dominio.
7. **Honestidad sobre el estado.** Si un requisito queda incompleto, documentar qué falta y su impacto en el README; no presentarlo como terminado.
8. **Configuración fuera del código.** Todo valor dependiente del entorno se lee de variables de entorno; documentarlas en `.env.example`.

## Comandos

```bash
cp .env.example .env
docker compose up -d --build   # Postgres + mock del proveedor en :4010
uv sync                        # entorno Python 3.12 (.venv)
uv run pytest                  # todos los tests (excluye los marcados llm)
uv run ruff check .            # lint
```

El mock se documenta en `mock/README.md`. Sus endpoints `/_control/*` solo se usan desde tests y demos; el código de `backend/` nunca debe llamarlos.
