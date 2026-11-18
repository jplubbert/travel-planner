# Travel Planner

Take-home: plataforma de planificación de viajes en grupo. Viajes compartidos con roles (organizador, editor, viewer), búsqueda en un proveedor externo mockeado, itinerario con costes en USD, reservas simuladas con reconciliación, documentos descargables y un asistente de IA de solo lectura que usa herramientas MCP.

- Enunciado (no modificar): [`assignment/`](assignment/) — [guía](assignment/candidate-guide.md), [PRD](assignment/prd.md), [contrato del proveedor](assignment/openAPI.json).
- Instrucciones para agentes de IA y reglas de trabajo: [`CLAUDE.md`](CLAUDE.md).
- Decisiones de arquitectura: [`docs/adr/`](docs/adr/). Supuestos: [`docs/assumptions.md`](docs/assumptions.md).
- **Documentación de entrega:**
  - [arquitectura](docs/architecture.md): piezas, decisiones, alternativas, garantías y límites;
  - [propuesta de despliegue](docs/deployment.md);
  - [guion de demo](docs/demo.md);
  - [uso de IA](docs/ai-usage.md).

## Stack

Python 3.12 · FastAPI · SQLAlchemy async · PostgreSQL · Alembic · httpx · pytest · MCP (SDK oficial 2.x, `MCPServer`) · Google Gemini con `google-genai` (`gemini-3.8-flash`, sin LangChain/LangGraph) · React + Vite + TypeScript (UI mínima, servida por nginx) · Docker Compose. Monolito modular con puertos y adaptadores; el mock del proveedor es un servicio aparte consumido solo por HTTP.

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
frontend/     UI mínima (React + Vite + TypeScript)
scripts/      e2e.sh: Playwright contra Compose levantado desde cero
tests/        pruebas y evaluaciones
docs/         ADRs y supuestos
```

## Puesta en marcha

```bash
cp .env.example .env           # opcional: GEMINI_API_KEY para el asistente
docker compose up --build      # UI en http://localhost:5173 · API en http://localhost:8000/docs · mock en http://localhost:4010/v1
```

En la UI, el selector de la cabecera cambia de usuario de prueba (`ana`, `beto`, `carla`, `dani`). Sin `GEMINI_API_KEY` todo funciona salvo el asistente, que responde `503 ASSISTANT_UNAVAILABLE`.

## Pruebas

```bash
docker compose up -d db
uv sync && uv run pytest                               # backend, mock y evaluaciones deterministas
uv run pytest evals/llm -m llm -s                      # evaluaciones con modelo: necesitan GEMINI_API_KEY (coste en evals/README.md)
cd frontend && npm ci && npm test                      # Vitest
./scripts/e2e.sh                                       # flujo completo en Docker desde cero, con Playwright
```

Inyección de errores, para comprobar que los tests detectan fallos reales: `python3 scripts/mutations/run.py` (ver [`scripts/mutations/README.md`](scripts/mutations/README.md), con los resultados de la última ejecución).

`scripts/e2e.sh` levanta un proyecto de Compose aparte, con volúmenes y puertos propios, así que no toca el stack local ni sus datos. Construye todas las imágenes, ejecuta los tests de Playwright a través del frontend y lo elimina al terminar. Ver [`frontend/README.md`](frontend/README.md).

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
| US-02: compartir con roles | ✅ Hecho: peticiones HTTP directas, documentos, suscripciones de estado (SSE) y tools del asistente (MCP), todos probados |
| US-03: buscar y comparar (completa / parcial / vacía / fallida, frescura, estimaciones, timeout, permisos) | ✅ Hecho, con tests contra el mock real por HTTP |
| US-04: itinerario (añadir con la cotización del proveedor, quitar, reordenar, actualizar cotización; marcas de expirado, no disponible, fuera de fechas, solapes, grupo; información que falta) | ✅ Hecho (ADR 0004) |
| US-05: costes por ítem, totales y presupuesto (estimado vs confirmado, redondeo documentado) | ✅ Hecho; probado también con reservas reales (confirmada, pendiente, fallida, rechazada) |
| US-06: ediciones concurrentes (versión por viaje, 409 con estado actual y cambios, 403 antes que 409) | ✅ Hecho, con tests de concurrencia contra Postgres |
| US-07: reservar con los términos revisados (re-chequeo con el proveedor, 409 con términos actuales) | ✅ Hecho, con tests contra el mock real |
| US-08: resultados inciertos y parciales (unknown/draft/rejected/pending/confirmed/failed, worker de reconciliación, sin duplicados) | ✅ Hecho, con tests contra el mock real (respuesta perdida, reinicio a mitad de envío, doble clic, 429/503) |
| US-09: generar documento (202, copia inmutable, worker con escritura atómica, estados, reintento, recuperación por lease) | ✅ Hecho (ADR 0005) |
| US-10: documento correcto (versión y hora, inmutable frente a ediciones, descarga verificada por hash, regeneración, barrido de huérfanos) | ✅ Hecho (ADR 0005) |
| Canal de estado SSE por viaje (avisos de reservas, documentos y viaje; mismos permisos; se cierra al perder acceso) | ✅ Hecho (ADR 0005) |
| US-11: asistente con recomendaciones basadas en datos (Gemini por adaptador, bucle propio, límites, llamadas a tools en la respuesta) | ✅ Hecho (ADR 0006). Evaluaciones con modelo: 5 de 5 con `gemini-3.8-flash` el 2026-11-17 (`evals/README.md`) |
| US-12: tools de solo lectura por MCP (SDK oficial, identidad por sesión firmada, permisos del usuario) | ✅ Hecho (ADR 0006), con evaluaciones deterministas |
| Frontend (ADR 0007): todos los flujos, 403/409 del backend tal cual, 409 con reaplicación, estado en vivo con relectura al reconectar | ✅ Hecho, con Vitest y Playwright contra Compose desde cero |
| Propuesta de despliegue, explicación de arquitectura, guion de demo y nota de uso de IA | ✅ Escritos ([`docs/`](docs/)). El despliegue es solo una propuesta: no se ha desplegado. En [`docs/demo.md`](docs/demo.md) se indica qué pasos se ensayaron y cuáles no |

## Criterios no cumplidos o con evidencia parcial

Revisados contra el PRD y la guía el 2026-11-18. Todo lo demás tiene tests automatizados (ver "Estado actual").

| Criterio | Situación |
| --- | --- |
| US-11: "explica cómo encajan las opciones con fechas, presupuesto, **preferencias** y **restricciones**, e identifica **huecos o conflictos**" | **Parcial.** Las instrucciones lo exigen, pero solo hay verificación automática del presupuesto, de los precios respaldados, de la falta de opciones, de no afirmar reservas y de los fallos de tools. No hay evaluación de que explique fechas, restricciones, huecos o conflictos. Las preferencias solo llegan en el mensaje del chat: el viaje no las guarda |
| Evaluaciones con modelo (guía) | Ejecutadas **una sola vez** (5 de 5, 2026-11-17, por el candidato). No se midió la tasa de acierto con `EVAL_REPEATS` ni el consumo real de tokens; el coste documentado es una estimación |
| Asistente desde la UI con clave real | **No ejecutado**. El e2e solo cubre el `503` sin clave |
| Proveedor limitado por tasa durante la reconciliación (guía: proveedores lentos o fallidos) | **Parcial.** El `429` se registra con `Retry-After` en `lastError`, pero el backoff de la reconciliación no lo usa y no hay limitador compartido entre workers |
| Diagnóstico de punta a punta (guía) | **Parcial.** El `X-Request-Id` no se propaga a los jobs del worker (sus logs muestran `request_id=None`); la reserva sí se puede seguir por `clientReference` y por el `requestId` del proveedor |
| Guion de demo (entregable) | Escrito. Los pasos 5 y 6 se ensayaron por API, no con clics en la UI. El 3 se ensayó con Playwright, no con DevTools a mano. El 7 no se ha ensayado con clave (ver la tabla de [`docs/demo.md`](docs/demo.md)) |
| Inyección de errores en el mock y en US-01 a US-06 | Se hizo durante el desarrollo, pero sus scripts no se conservaron; solo consta en los mensajes de commit. Las suites de `scripts/mutations/` cubren reservas, documentos, MCP y asistente, secretos y UI |
| Harness: configuración de la herramienta | No hay configuración de Claude Code versionada (ni permisos ni hooks): se usó la configuración por defecto, y `.gitignore` excluye un posible `.claude/settings.local.json`. Sí se versionan `CLAUDE.md`, el prompt `/historia` y los scripts |
| Despliegue | Solo propuesta, como permite la guía; no se ha desplegado |

## Despliegue propuesto

Resumen. El detalle está en [`docs/deployment.md`](docs/deployment.md). **No se ha desplegado**: solo se ha ejecutado Docker Compose en local.

- **Servicios:**
  - `frontend` (nginx con el bundle estático y proxy de `/api`) y `api` (FastAPI): sin estado, varias réplicas detrás de un balanceador, con salud en `/healthz` y `/readyz`;
  - `worker`: escala horizontalmente, porque los jobs se reclaman con `FOR UPDATE SKIP LOCKED` y lease;
  - un job de migraciones (`alembic upgrade head`) antes de cada despliegue.
- **Datos:**
  - PostgreSQL gestionado con copias y recuperación a un punto en el tiempo;
  - documentos en S3 o compatible, mediante un adaptador del puerto `DocumentStorage` (hoy es un volumen local compartido).
- **Secretos:** `DATABASE_URL`, `PROVIDER_TOKEN`, `MCP_TOKEN_SECRET` y `GEMINI_API_KEY`, desde un gestor de secretos. Toda la configuración se lee del entorno (`.env.example`).
- **Observabilidad:** hoy hay `X-Request-Id` de punta a punta, logs del proveedor con su `requestId`, y `lastError` en reservas y documentos. Falta añadir métricas de la cola y de reservas sin resolver, alertas y trazas.
- **Para producción:**
  - identidad real (OIDC) en lugar de `X-User-Id`;
  - S3 para documentos;
  - `LISTEN/NOTIFY` en lugar de sondear PostgreSQL por cada stream SSE;
  - limitador de tasa compartido y uso de `Retry-After` en la reconciliación.

## Uso de IA en el desarrollo

Se desarrolló con Claude Code; el asistente de la aplicación usa Gemini, y son cosas distintas. El harness versionado está formado por:
- [`CLAUDE.md`](CLAUDE.md);
- el prompt reutilizable [`.claude/commands/historia.md`](.claude/commands/historia.md);
- [`docs/adr/`](docs/adr/) y [`docs/assumptions.md`](docs/assumptions.md);
- [`scripts/`](scripts/) (e2e e inyección de errores);
- [`evals/`](evals/).

La nota sobre herramientas, modelos, cómo se dirigió el trabajo, los errores de la IA detectados y lo que decidió el candidato está en [`docs/ai-usage.md`](docs/ai-usage.md).
