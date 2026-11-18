# Uso de IA en el desarrollo

## Herramientas y modelos

| Uso | Herramienta | Modelo |
| --- | --- | --- |
| Desarrollo (código, tests, documentación, ensayos en Docker) | Claude Code (CLI) | Claude Opus 5.5 (`claude-opus-5-5`), el modelo que informa el entorno de las sesiones de trabajo |
| Asistente de la aplicación (US-11) | SDK `google-genai` | `gemini-3.8-flash`, verificado en la documentación de Google el 2026-11-17 (ADR 0006) |

Son cosas distintas: Claude Code es la herramienta con la que se construyó el proyecto, y Gemini es el modelo que usa el asistente dentro de la aplicación. El repositorio no depende de Claude en tiempo de ejecución.

## El harness versionado y cómo usarlo

| Pieza | Para qué |
| --- | --- |
| [`CLAUDE.md`](../CLAUDE.md) | Instrucciones del agente: resumen, decisiones tomadas, reglas de trabajo y comandos. Claude Code lo carga automáticamente al abrir el repo. |
| [`.claude/commands/historia.md`](../.claude/commands/historia.md) | Prompt reutilizable: `/historia US-07` en Claude Code ejecuta el ciclo de una historia (PRD → tests → supuestos y ADR → inyección de errores → checks → commit). Se escribió al cerrar, **destilado** de los prompts por historia que se usaron. Durante el desarrollo esos prompts se escribían a mano en cada sesión. |
| [`docs/adr/`](adr/) y [`docs/assumptions.md`](assumptions.md) | Memoria de decisiones y supuestos. El agente debe registrar ahí lo que decide, y no puede cambiar una decisión aceptada sin un ADR nuevo. |
| [`scripts/mutations/`](../scripts/mutations/README.md) | Inyección de errores para comprobar que los tests detectan fallos reales. |
| [`scripts/e2e.sh`](../scripts/e2e.sh) | Flujo completo en Docker desde cero, con Playwright. |
| [`evals/`](../evals/README.md) | Evaluaciones del MCP y del asistente: deterministas, y con modelo marcadas `llm`. |

**Requisitos para usarlo:**
- Claude Code con acceso a un modelo de Anthropic;
- Docker con Compose;
- `uv` (Python 3.12);
- Node y npm (probado con Node 26, la versión de la imagen Docker);
- para las evaluaciones con modelo, una clave de Gemini.

No hay configuración de Claude Code versionada (permisos, hooks): se usó la configuración por defecto. `.gitignore` excluye `.claude/settings.local.json`, que depende de cada máquina.

## Cómo se dirigió el trabajo

- **Primero el marco, sin código.** La primera sesión solo creó `CLAUDE.md`, el ADR 0001, los supuestos, el esqueleto, Compose y el README, con la instrucción explícita de leer la guía, el PRD y el contrato completos y de **no modificar nunca `assignment/`**.
- **Un prompt por historia o grupo de historias**, con los criterios a cubrir y las condiciones del candidato. Por ejemplo: "US-07 y US-08 según ADR 0003, con la lista detallada; repite los tests de US-05 con reservas reales; inyecta errores; commit". Cada prompt exigía releer el PRD, tests que prueben cada criterio, supuestos y ADR, y un commit al final (reglas de `CLAUDE.md`).
- **Tests contra dependencias reales:**
  - PostgreSQL real;
  - el mock del proveedor como proceso HTTP real;
  - un uvicorn real para el SSE;
  - en el frontend, Playwright contra Compose.
  - Los fallos se provocan con `/_control` solo desde los tests, y un test de arquitectura impide que el backend lo llame.
- **Inyección de errores en cada historia.** Tras cada bloque se introducían fallos deliberados (quitar una comprobación, cambiar un estado, no persistir antes de enviar…) y se comprobaba que algún test fallara. Cuando una mutación sobrevivía, se escribía el test que faltaba o se justificaba como defensa en profundidad, verificándolo al quitar las dos capas. Los resultados actuales están en [`scripts/mutations/README.md`](../scripts/mutations/README.md).
- **Ensayos en Docker** además de los tests:
  - matar la API a mitad de una reserva;
  - borrar el archivo de un documento;
  - un cliente MCP externo por red real.
- **Revisión del candidato** en cada entrega del agente. Varias correcciones vinieron de esa revisión (abajo).

## Errores de la IA detectados y corregidos

**Detectados por los tests o por el propio agente**

| Error | Cómo se detectó | Corrección |
| --- | --- | --- |
| Bajo READ COMMITTED, una validación posterior podía ver un commit más nuevo y responder `422` en vez de `409` | Test de concurrencia intermitente | `SELECT … FOR UPDATE` en la comprobación de versión, y un test determinista (ADR 0001) |
| `JSONB` guardaba `None` como JSON `null`, así que `IS NOT NULL` era cierto | Tests de integridad | `JSONB(none_as_null=True)` y normalización en la migración 0004 |
| `ON CONFLICT` con un predicado de índice parcial parametrizado fallaba en la sexta ejecución (plan genérico de asyncpg) | Test de cola de jobs | Predicado literal en `index_where` |
| Se usó `mcp.server.fastmcp`, que ya no existe en el SDK 2.x (ahora `MCPServer`), además de atributos en camelCase | Errores de importación y de atributo | `MCPServer` y campos snake_case; `CLAUDE.md` corregido |
| Un `ModelError` dentro del grupo de tareas del cliente MCP llegaba envuelto en un `ExceptionGroup` y daba `500` | Eval determinista | Se captura dentro del contexto MCP y se responde `502` |
| Tests de mutación de las primeras historias que no detectaban la ausencia de un lock | Mutación superviviente | Test con `yield_in_critical_section` |
| Durante la redacción de `docs/deployment.md` se afirmó que la reconciliación respeta `Retry-After` | Revisión contra el código antes del commit | Corregido en el documento. Después, a petición del candidato, se implementó: la reconciliación ya no reintenta antes del `Retry-After` |
| Una mutación de la UI (parser SSE) dejaba código sintácticamente inválido, así que "se detectaba" por un error de compilación y no por un test | Lectura de la salida del runner | Mutación reescrita para ser válida; ahora la detectan los tests |
| El e2e de la reserva `pending` volvía a abrir la pestaña tras confirmar, y la relectura podía ocultar un evento SSE perdido: la mutación "ignorar eventos de reserva" se detectó un día y otro no | Repetir la suite de mutaciones con el runner nuevo | Test corregido para que solo un evento en vivo pueda actualizar la tarjeta; detectada en 2 de 2 ejecuciones |

**Detectados por el candidato**

| Error | Corrección |
| --- | --- |
| La fixture `llm_app` era síncrona y fallaba con `RuntimeError: no running event loop` al correr las evals con modelo. **El traceback imprimió la clave de Gemini completa** | Fixture async, y `GEMINI_API_KEY` (después también `MCP_TOKEN_SECRET`) como `SecretStr`, con tests que comprueban que no aparece en `repr`, `str` ni tracebacks |
| Una fecha errónea (2026-11-18 en lugar de 2026-11-17) en la migración 0004 y en el ADR 0003 | Corregida |
| Durante una prueba en Docker, el agente no comprobó que `.env` tenía `GEMINI_API_KEY` e hizo **una llamada real a Gemini sin pretenderlo** (respondió `503`; la app devolvió `502` correctamente) | El agente lo informó en vez de ocultarlo. Desde entonces `scripts/e2e.sh` vacía la clave |
| El primer modelo elegido para el asistente (Anthropic) no era el que quería el candidato | ADR 0006, que sustituye esa decisión |
| El agente construyó el stack de ensayo y ensayó la demo **mientras corría la inyección de errores en segundo plano**, así que la imagen pudo incluir código mutado (`if False:` en `reservations.py`) | Se invalidaron esos ensayos. Se comprobó que el código quedó restaurado y se pasó la suite completa. Se reconstruyó el stack sin caché, verificando que el código de las imágenes era idéntico byte a byte al del repo, y se repitieron los ensayos. Nueva regla 9 en `CLAUDE.md`: nada en paralelo con las mutaciones |

## Qué decidió el candidato

- **Stack y arquitectura:** Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL y Alembic; monolito modular hexagonal; mock como servicio aparte consumido solo por HTTP; MCP de solo lectura; Docker Compose para todo.
- **Proveedor del asistente:** primero Anthropic y después **Gemini con `google-genai`**, sin LangChain ni LangGraph. Pidió que el modelo Flash vigente se eligiera **verificándolo en la documentación, no de memoria**.
- **Concurrencia:** `400 VERSION_REQUIRED` en lugar de `428` cuando falta la versión.
- **Plan de trabajo:** plan por días y orden de las historias, con el código congelado el viernes a las 18:00.
- **Seguridad:** secretos como `SecretStr`. Reintentos de Gemini solo para 429, 503 y el 403 esporádico, nunca para 400, 401 o 402; el agente añadió además los 5xx y lo informó.
- **Frontend:** React + Vite + TypeScript sin librería de UI; SSE leído con `fetch`; 409 con reaplicación; la UI no es la barrera de permisos; orden de bloques con un commit por bloque.
- **Método:** inyectar errores en cada historia, un commit por tarea, y no presentar como hecho nada sin test ni ejecución.
- **Evaluaciones con modelo:** las ejecutó el propio candidato con su clave (5 de 5 el 2026-11-17). El agente no las ejecutó, tampoco las 2 de US-11 añadidas el 2026-11-18.
- **Prueba del asistente desde la UI con clave real:** la hizo el candidato el 2026-11-17 (ver `docs/demo.md`, paso 7).
- **Cierre de huecos del 2026-11-18:** respetar `Retry-After` en la reconciliación, propagar el `X-Request-Id` a los jobs del worker y evaluar con modelo la relación con fechas y restricciones y los conflictos de horario.

## Cómo se verificó la salida

- **`uv run pytest`:** backend, mock y evals deterministas; 542 pasan (5 de modelo excluidos), 2026-11-18.
- **`npm test`:** 31 tests de Vitest pasan, 2026-11-18.
- **`scripts/e2e.sh`:** 5 de 5, desde cero, 2026-11-18 (después de corregir el test de la reserva `pending`).
- **Checks estáticos:** `ruff`, `mypy --strict` y `tsc` estricto.
- **Inyección de errores** (`scripts/mutations/`).
- **Ensayos en Docker** del guion de demo ([`docs/demo.md`](demo.md) indica qué pasos se ensayaron y cuáles no).
- **Revisión del candidato:** lectura de los diffs y de los ADR en cada entrega.
