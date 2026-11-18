# Arquitectura

Explicación corta. El detalle de cada decisión, con sus alternativas, está en los ADR:

| ADR | Tema |
| --- | --- |
| [0001](adr/0001-concurrencia-por-version.md) | Concurrencia por versión optimista del viaje |
| [0002](adr/0002-mock-proveedor-con-control.md) | Mock del proveedor con fixtures, controles fuera del contrato y reloj |
| [0003](adr/0003-estados-de-reserva-y-reconciliacion.md) | Estados de reserva y reconciliación |
| [0004](adr/0004-copia-de-oferta-y-frescura.md) | Copia de la oferta en el ítem y política de frescura |
| [0005](adr/0005-jobs-documentos-y-canal-de-estado.md) | Jobs en PostgreSQL, documento como copia inmutable y canal de estado SSE |
| [0006](adr/0006-mcp-asistente-y-proveedor-de-modelo.md) | MCP en el mismo proceso, identidad firmada, bucle propio y modelo intercambiable |
| [0007](adr/0007-frontend-mismo-origen-y-estado-en-vivo.md) | Frontend en el mismo origen, SSE con `fetch` y la UI que no es barrera de permisos |

## Piezas

```
navegador ──HTTP──▶ nginx (frontend/) ──/api──▶ API FastAPI ──SQL──▶ PostgreSQL
                                                 │   ▲                ▲
                                     /mcp (MCP)  │   │ jobs           │
                                     asistente ◀─┘   │                │
                                                 │   worker ───────────┘
                                                 │     │
                                                 └─────┴──HTTP (openAPI.json)──▶ mock del proveedor
```

- **Monolito modular con puertos y adaptadores** (`backend/app/`).
  - `domain/` contiene las reglas: permisos, versión, itinerario, costes, máquina de estados de reservas y documentos. No importa frameworks; `tests/unit/test_architecture.py` lo comprueba.
  - `ports/` declara lo que el dominio necesita: repositorios, proveedor, almacenamiento, reloj y modelo.
  - `adapters/` lo implementa: SQLAlchemy, httpx, disco, Gemini y cliente MCP.
  - `api/`, `mcp/` y `workers/` son puntos de entrada sin reglas propias.
- **API** (FastAPI): la identidad de prueba llega en `X-User-Id`. `load_access` del dominio decide el acceso: `404` para un no participante, `403` si falta rol.
- **Worker** (`python -m app.workers`): reclama jobs de la tabla `jobs` con `FOR UPDATE SKIP LOCKED` y lease. Hay dos tipos: reconciliar reservas (`sync_booking`) y generar documentos. También hace barridos de envíos interrumpidos y de archivos huérfanos. Se puede escalar a varias réplicas.
- **PostgreSQL**: el estado. Migraciones con Alembic, un historial `trip_changes` y triggers que hacen inmutables los estados terminales y los datos aceptados de reservas y documentos.
- **Mock del proveedor** (`mock/`): un servicio aparte que implementa `assignment/openAPI.json`. La aplicación solo usa `/v1`; `/_control` es para tests y demos, y un test de arquitectura impide que `backend/` lo llame.
- **MCP**: `MCPServer` del SDK oficial, montado en `/mcp`, con 7 tools de solo lectura que reutilizan los casos de uso y `load_access`. El asistente es un cliente MCP con un bucle propio y usa Gemini a través del puerto `ChatModel`.
- **Frontend**: React + Vite. nginx lo sirve y hace de proxy de `/api`. El estado en vivo llega por SSE y se relee con `GET`.

## Flujo de una reserva con respuesta perdida

ADR 0003. Ensayado en Docker (ver [demo](demo.md), paso 5).

1. **Revisión de términos.** El editor envía `expectedRevision` y `expectedPrice`.
   - La API consulta la oferta al proveedor (`GET /offers/{id}`).
   - Si la revisión o el precio cambiaron, responde `409 TERMS_CHANGED` con los términos actuales, sin enviar nada.
   - Si la oferta no está disponible, responde `409 OFFER_UNAVAILABLE`.
2. **Registro antes de enviar.** Se guarda la reserva en `submitted`, con `clientReference` e `Idempotency-Key` propias, y se confirma la transacción. Nada sale hacia el proveedor sin quedar registrado antes. Un índice parcial impide dos intentos activos para el mismo ítem.
3. **Envío.** `POST /bookings`, y el proveedor crea la reserva. Supongamos que la respuesta no llega: timeout, conexión caída o la API muere.
   - Si el error dice que la petición pudo haber llegado (`delivery_uncertain`), la reserva pasa a `unknown`.
   - Si el proceso murió, queda en `submitted`.
   - En ningún caso aparece como reservada: `booked=false`, y `nextStep` dice que no se reserve otra vez.
4. **Reconciliación.**
   - El worker programa un job `sync_booking` (o el barrido detecta un `submitted` de más de `BOOKING_SUBMITTED_STALE_SECONDS`).
   - Primero consulta `GET /bookings/by-reference/{clientReference}`. Si existe, adopta el resultado del proveedor (`confirmed`, `pending` o `failed`) y guarda la oferta y el precio aceptados.
   - Si no existe y pasó el plazo de gracia, **reenvía con la misma `Idempotency-Key`**: el proveedor devuelve la misma reserva o la crea una sola vez.
5. **Aviso.** El canal SSE avisa del cambio y la UI relee con `GET`.

## Garantías

| Garantía | Mecanismo | Evidencia |
| --- | --- | --- |
| Ninguna edición se pierde en silencio | Versión del viaje con compare-and-set y `SELECT … FOR UPDATE`. El `409` trae el estado actual y `changesSinceYourVersion`. El `403` va antes que el `409` | `tests/integration/test_us06_concurrency.py`; e2e del 409 con reaplicación |
| Permisos en todas las entradas | `load_access` en API, SSE (re-chequeo en cada sondeo), descargas y tools MCP. La identidad MCP va en un token firmado y nunca es un argumento | `test_us02_sharing.py`, `test_status_channel.py`, `evals/deterministic/test_mcp_contract.py`, tests de arquitectura |
| Una reserva solo es "reservada" si el proveedor la confirmó | Máquina de estados en el dominio. `booked` solo es cierto en `confirmed`, y un trigger impide revertir estados terminales | `tests/unit/test_booking_states.py`, `test_us07_us08_bookings.py`, Vitest y e2e de la UI |
| Sin reservas duplicadas | `Idempotency-Key` estable por intento, un intento activo por ítem (índice parcial) y reconciliación por referencia antes de reenviar | `test_us07_us08_bookings.py` (doble clic, respuesta perdida, reinicio a mitad de envío) |
| Un documento listo es completo y corresponde a su versión | Copia inmutable al pedirlo, escritura atómica (temporal, `fsync` y `rename`), sha256 verificado en cada descarga, y regeneración desde la copia si falta o no coincide | `test_us09_us10_documents.py`; ensayo de la demo |
| Precio estimado frente a confirmado | Se copia la oferta en el ítem con `validUntil`. Los costes separan estimado y confirmado, y el precio aceptado es inmutable | `test_us04_itinerary.py`, `test_us05_costs.py` |
| Totales en USD reproducibles | `Decimal`, sin coma flotante, *half-up* a centavos, documentado en `rounding` | `tests/unit/test_costs.py` |
| Recuperación tras desconexión | Los eventos SSE solo avisan; con `ready` en cada (re)conexión la UI relee con `GET` | `test_status_channel.py`; e2e "al reconectar relee" |
| Proveedor lento, caído o limitado por tasa | Plazo total por llamada (`PROVIDER_TIMEOUT_SECONDS`), búsqueda parcial o fallida explícita, `lastError` y `nextStep` en las reservas, y la reconciliación nunca reintenta antes del `Retry-After` | `test_us03_search.py`, `test_provider_client.py`, `test_us07_us08_bookings.py` |
| Una operación en segundo plano se puede rastrear | El `X-Request-Id` de la petición pasa al job, y el worker lo usa en sus logs y en las llamadas al proveedor | `test_request_id_propagation.py` |
| El asistente no inventa ni escribe | Las tools son de solo lectura (test de arquitectura). Las instrucciones exigen datos de las tools, y hay verificadores automáticos de precios y afirmaciones | `evals/` (deterministas por defecto; con modelo, 5 de 5 el 2026-11-17) |

Los tests se comprobaron también inyectando errores: [`scripts/mutations/`](../scripts/mutations/README.md).

## Alternativas descartadas (resumen)

- **Concurrencia:**
  - bloqueo pesimista o "último gana": la primera opción no sirve para ediciones largas y la segunda pierde cambios;
  - fusión automática: compleja y opaca.
- **Cola de trabajos:** Redis/Celery o un broker externo. Una pieza más que operar; PostgreSQL ya da transacciones con el estado (ADR 0005).
- **Estado en vivo:** WebSockets o long-polling. SSE basta para avisos de servidor a cliente (ADR 0005).
- **MCP:** servicio MCP aparte, o llamar a las funciones sin MCP. El primero duplica la autorización; lo segundo incumple US-12.
- **Asistente:** LangChain o LangGraph, o el *automatic function calling* del SDK. Ocultan los límites y el registro de llamadas (ADR 0006).
- **Frontend:** CORS frente a mismo origen, y librerías de UI o de estado (ADR 0007).

## Límites conocidos

- **Identidad:** cabecera de prueba `X-User-Id`, sin autenticación real (el PRD lo permite). Los tokens MCP solo los emite la app para el asistente; no hay emisión para clientes MCP externos.
- **Documentos:** se guardan en un volumen local compartido por la API y el worker. En producción irían a almacenamiento de objetos ([despliegue](deployment.md)).
- **SSE:**
  - cada stream consulta PostgreSQL cada `EVENTS_POLL_SECONDS`, lo que escala linealmente con las conexiones abiertas;
  - la mejora natural es `LISTEN/NOTIFY`;
  - con varias réplicas de la API hay que mantener sin buffer el proxy de `/trips/{id}/events`.
- **Asistente:**
  - comparte proceso y pool de conexiones con la API;
  - las evaluaciones con modelo son probabilísticas: los 5 casos originales se ejecutaron una vez (5 de 5); los 2 de US-11 añadidos después (fechas y restricciones, conflicto de horario) aún no; y no se midió el consumo de tokens.
- **Reconciliación:**
  - concluir `rejected` tras reenviar depende de que el proveedor cumpla la idempotencia ante peticiones idénticas en vuelo (ADR 0003, riesgo documentado);
  - el `Retry-After` se respeta por job, pero no hay un limitador de tasa compartido entre workers.
- **Cotizaciones:** se actualizan a mano, ítem a ítem (ADR 0004). Los resultados de búsqueda no se cachean.
- **Sin cancelación ni reembolsos** (fuera de alcance). Un ítem con una reserva activa no se puede quitar.
- **Datos de ejemplo:** las fixtures cubren Lisboa, Madrid y París en junio de 2030, con reglas de itinerario simplificadas (`docs/assumptions.md`).
