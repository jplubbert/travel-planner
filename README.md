# Travel Planner

Take-home: plataforma de planificación de viajes en grupo. Viajes compartidos con roles (organizador, editor, viewer), búsqueda en un proveedor externo mockeado, itinerario con costes en USD, reservas simuladas con reconciliación, documentos descargables y un asistente de IA de solo lectura que usa herramientas MCP.

- Enunciado (no modificar): [`assignment/`](assignment/) — [guía](assignment/candidate-guide.md), [PRD](assignment/prd.md), [contrato del proveedor](assignment/openAPI.json).
- Instrucciones para agentes de IA y reglas de trabajo: [`CLAUDE.md`](CLAUDE.md).
- Decisiones de arquitectura: [`docs/adr/`](docs/adr/). Supuestos: [`docs/assumptions.md`](docs/assumptions.md).

## Stack

Python 3.12 · FastAPI · SQLAlchemy async · PostgreSQL · Alembic · httpx · pytest · MCP (FastMCP, SDK oficial) · Anthropic SDK (`claude-sonnet-4-5`, sin LangChain/LangGraph) · Docker Compose. Monolito modular con puertos y adaptadores; el mock del proveedor es un servicio aparte consumido solo por HTTP.

## Estructura

```
backend/app/
  domain/     núcleo sin dependencias de framework
  ports/      interfaces que necesita el dominio
  adapters/   Postgres, cliente HTTP del proveedor, almacenamiento…
  api/        HTTP (FastAPI)
  mcp/        servidor MCP de solo lectura
  assistant/  asistente que consume MCP
  workers/    documentos y reconciliación en segundo plano
mock/         proveedor externo simulado (FastAPI)
frontend/     UI mínima
tests/        pruebas y evaluaciones
docs/         ADRs y supuestos
```

## Puesta en marcha (estado actual)

```bash
cp .env.example .env
docker compose up -d --build   # Postgres + API (http://localhost:8000/docs) + worker + mock (http://localhost:4010/v1)
uv sync && uv run pytest       # tests (los de integración necesitan el servicio db en marcha)
```

Ver [`mock/README.md`](mock/README.md) para las fixtures y los escenarios de fallo.

## Plan (4 días, entrega viernes 20 nov 2026 23:59)

| Día | Fecha | Objetivo |
| --- | --- | --- |
| 1 | mar 17 nov | **Mock del proveedor** completo según el contrato (búsqueda con resultados parciales, quotes con expiración/revisión, reservas idempotentes, lookup por referencia, fallos inyectables, persistencia). **Modelo de dominio** de viajes y permisos, migraciones. **US-01, US-02, US-03, US-04, US-05, US-06.** |
| 2 | mié 18 nov | **US-07, US-08** reservas (revalidación de términos, idempotencia, estado desconocido, reconciliación). **US-09, US-10** documentos (generación en segundo plano, estados, recuperación, descarga autorizada). **Servidor MCP** de solo lectura, **asistente**, **frontend mínimo**. **Objetivo: todo el PRD funcional al final del día.** |
| 3 | jue 19 nov | Tests de fallos y concurrencia, evaluaciones del asistente (deterministas y con modelo), README, explicación de arquitectura, guion de demo. |
| 4 | vie 20 nov | Colchón, revisión final y entrega. **Código congelado a las 18:00.** |

Criterio transversal: ninguna historia se considera hecha sin tests que prueben sus criterios de aceptación (ver `CLAUDE.md`).

## Estado actual

| Área | Estado |
| --- | --- |
| Andamiaje del repo, CLAUDE.md, ADR 0001, Postgres en Compose | ✅ Hecho |
| Mock del proveedor (`mock/`, ADR 0002): 5 operaciones del contrato, escenarios de fallo, reloj, persistencia; 99 tests validados contra el schema | ✅ Hecho |
| Modelo de dominio y persistencia (Trip, TripMember, ItineraryItem, Booking, Document, Job), migraciones Alembic, usuarios de prueba | ✅ Hecho |
| Worker de jobs en segundo plano (`python -m app.workers`, servicio `worker`) | ✅ Hecho |
| ADR 0003: estados de reserva y reconciliación (máquina de estados en el dominio, con tests) | ✅ Hecho |
| US-01: crear viaje y volver a él (persistencia tras reinicio, validación accionable, edición con versión) | ✅ Hecho, con tests contra Postgres |
| US-02: compartir con roles | 🟡 Parcial: las peticiones HTTP directas están cubiertas y probadas (404 para no participantes, 403 por rol). Faltan los criterios sobre documentos, suscripciones de estado y herramientas del asistente, que se probarán al construir esas piezas |
| US-03: buscar y comparar (completa / parcial / vacía / fallida, frescura, estimaciones, timeout, permisos) | ✅ Hecho, con tests contra el mock real por HTTP |
| US-04: itinerario (añadir con la cotización del proveedor, quitar, reordenar, actualizar cotización; marcas de expirado, no disponible, fuera de fechas, solapes, grupo; información que falta) | ✅ Hecho (ADR 0004) |
| US-05: costes por ítem, totales y presupuesto (estimado vs confirmado, redondeo documentado) | ✅ Hecho; probado también con reservas reales (confirmada, pendiente, fallida, rechazada) |
| US-06: ediciones concurrentes (versión por viaje, 409 con estado actual y cambios, 403 antes que 409) | ✅ Hecho, con tests de concurrencia contra Postgres |
| US-07: reservar con los términos revisados (re-chequeo con el proveedor, 409 con términos actuales) | ✅ Hecho, con tests contra el mock real |
| US-08: resultados inciertos y parciales (unknown/draft/rejected/pending/confirmed/failed, worker de reconciliación, sin duplicados) | ✅ Hecho, con tests contra el mock real (respuesta perdida, reinicio a mitad de envío, doble clic, 429/503) |
| US-09 … US-12 | ⏳ Pendiente |
| MCP + asistente | ⏳ Pendiente |
| Frontend | ⏳ Pendiente (stack por definir) |
| Despliegue propuesto, demo, nota de uso de IA | ⏳ Pendiente |

## Uso de IA en el desarrollo

El proyecto se desarrolla con Claude Code. El harness versionado es [`CLAUDE.md`](CLAUDE.md) (reglas y decisiones) junto con `docs/adr/` y `docs/assumptions.md`. La nota completa sobre cómo se usó la IA y cómo se verificó su salida se añadirá antes de la entrega.
