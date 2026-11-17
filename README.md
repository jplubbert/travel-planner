# Travel Planner

Take-home: plataforma de planificación de viajes en grupo. Viajes compartidos con roles (organizador, editor, viewer), búsqueda en un proveedor externo mockeado, itinerario con costes en USD, reservas simuladas con reconciliación, documentos descargables y un asistente de IA de solo lectura que usa herramientas MCP.

- Enunciado (no modificar): [`assignment/`](assignment/) — [guía](assignment/candidate-guide.md), [PRD](assignment/prd.md), [contrato del proveedor](assignment/openAPI.json).
- Instrucciones para agentes de IA y reglas de trabajo: [`CLAUDE.md`](CLAUDE.md).
- Decisiones de arquitectura: [`docs/adr/`](docs/adr/). Supuestos: [`docs/assumptions.md`](docs/assumptions.md).

## Stack

Python 3.12 · FastAPI · SQLAlchemy async · PostgreSQL · Alembic · httpx · pytest · MCP (FastMCP, SDK oficial) · Docker Compose. Monolito modular con puertos y adaptadores; el mock del proveedor es un servicio aparte consumido solo por HTTP.

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
docker compose up -d db
```

Por ahora solo arranca PostgreSQL.

## Plan (4 días, entrega viernes 20 nov 2026 23:59)

| Día | Fecha | Objetivo |
| --- | --- | --- |
| 1 | mar 17 nov | Andamiaje, ADRs iniciales. **Mock del proveedor** completo según el contrato (búsqueda con resultados parciales, quotes con expiración/revisión, reservas idempotentes, lookup por referencia, fallos inyectables, persistencia). Modelo de dominio de viajes y permisos, migraciones. **US-01, US-02.** |
| 2 | mié 18 nov | Cliente HTTP del proveedor (timeouts, 429/503, mapeo de errores). **US-03** búsqueda, **US-04** itinerario, **US-05** costes y redondeo, **US-06** concurrencia optimista (ADR 0001). Tests de concurrencia reales contra Postgres. |
| 3 | jue 19 nov | **US-07/US-08** reservas: revalidación de términos, idempotencia, estado desconocido y worker de reconciliación. **US-09/US-10** documentos: generación en segundo plano, estados, recuperación y descarga autorizada. |
| 4 | vie 20 nov | **US-11/US-12** servidor MCP de solo lectura y asistente con permisos por usuario; evaluaciones deterministas y con modelo. Frontend mínimo para los flujos. Docker Compose completo, README final, explicación de arquitectura, nota de uso de IA, guion de demo. |

Criterio transversal: ninguna historia se considera hecha sin tests que prueben sus criterios de aceptación (ver `CLAUDE.md`).

## Estado actual

| Área | Estado |
| --- | --- |
| Andamiaje del repo, CLAUDE.md, ADR 0001, Postgres en Compose | ✅ Hecho |
| Mock del proveedor | ⏳ Pendiente |
| US-01 … US-12 | ⏳ Pendiente |
| MCP + asistente | ⏳ Pendiente |
| Frontend | ⏳ Pendiente (stack por definir) |
| Despliegue propuesto, demo, nota de uso de IA | ⏳ Pendiente |

## Uso de IA en el desarrollo

El proyecto se desarrolla con Claude Code. El harness versionado es [`CLAUDE.md`](CLAUDE.md) (reglas y decisiones) junto con `docs/adr/` y `docs/assumptions.md`. La nota completa sobre cómo se usó la IA y cómo se verificó su salida se añadirá antes de la entrega.
