# mock — Proveedor externo simulado

Servicio FastAPI independiente que implementa [`assignment/openAPI.json`](../assignment/openAPI.json). La aplicación lo consume **solo por HTTP bajo `/v1`**. Los endpoints `/_control/*` quedan fuera del contrato y sirven únicamente para tests y demo; la aplicación nunca debe llamarlos. Diseño y alternativas: [ADR 0002](../docs/adr/0002-mock-proveedor-con-control.md).

## Cómo correrlo

```bash
# Con Docker (recomendado): puerto 4010, datos en el volumen "mockdata"
docker compose up -d --build provider-mock

# Local, desde la raíz del repo
uv sync
cd mock && uv run python -m provider_mock          # o: uv run uvicorn provider_mock.app:create_app --factory --port 4010
```

- URL base del contrato: `http://localhost:4010/v1`. Healthcheck: `GET /healthz`. Swagger del mock: `/_docs`.
- Autenticación: `Authorization: Bearer travel-demo-token`. Cada token es un *caller* distinto, y reservas, referencias e idempotencia se aíslan por caller.
- Logs: una línea por petición con `request_id` (el mismo que devuelve `X-Request-Id` y el campo `requestId` de los errores) y el caller.

| Variable | Defecto | Uso |
| --- | --- | --- |
| `MOCK_TOKENS` | `travel-demo-token` | Tokens aceptados, separados por comas |
| `MOCK_STATE_FILE` | `data/mock-state.json` (`/data/...` en Docker) | Archivo de persistencia |
| `MOCK_QUOTE_TTL_SECONDS` | `900` | Validez de cada quote (`validUntil = ahora + TTL`) |
| `MOCK_SUPPLIER_DEADLINE_SECONDS` | `2` | Espera de una búsqueda agregada ante un supplier en `timeout` |
| `MOCK_HOST` / `MOCK_PORT` | `0.0.0.0` / `4010` | Solo para `python -m provider_mock` |

## Tests

```bash
uv run pytest mock/tests
```

Cada respuesta se valida contra el schema del contrato que corresponde a su operación y código de estado. Se usa `jsonschema` (Draft 2020-12) con `$ref` resueltos sobre el propio `openAPI.json` y con comprobación de formatos (`date`, `date-time`). También se comprueba que el código de estado esté declarado para la operación y que exista `Retry-After` en los 429. `test_contract_harness.py` verifica que el validador rechaza datos inválidos, para que los demás tests no pasen sin comprobar nada.

Cobertura: autenticación en las 5 operaciones, validación de requests, búsqueda completa/parcial/vacía/fallida, refresco de quotes expirados, `OFFER_CHANGED` (revisión, precio, expiración), `OFFER_UNAVAILABLE`, idempotencia (replay, orden de propiedades, `IDEMPOTENCY_CONFLICT`), `REFERENCE_CONFLICT`, aislamiento por caller, peticiones concurrentes, pending → confirmed/failed, persistencia tras reinicio y respuesta perdida.

## Fixtures

Tres suppliers lógicos. Cada categoría la ofrecen dos de ellos, así que siempre se pueden demostrar resultados parciales:

| Supplier | Categorías |
| --- | --- |
| `supplier-a` | accommodation, activity |
| `supplier-b` | transport, accommodation |
| `supplier-c` | transport, activity |

Los destinos forman un posible viaje encadenado en junio de 2030. Las horas llevan el offset local del destino:

| Destino | Ventana de fixtures | Offset |
| --- | --- | --- |
| Lisbon | 2030-06-10 … 2030-06-14 | +01:00 |
| Madrid | 2030-06-14 … 2030-06-18 | +02:00 |
| Paris | 2030-06-18 … 2030-06-22 | +02:00 |

- **Transporte** (14 ofertas): London→Lisbon, Madrid→Lisbon, Lisbon→Madrid, London→Madrid, Madrid→Paris, Lisbon→Paris, Paris→London. `destination` es la ciudad de llegada y la búsqueda requiere `origin`.
- **Alojamiento** (12 ofertas): 4 por destino, cada una con capacidad máxima (`max_travelers`).
- **Actividades** (13 ofertas): de 4 a 5 por destino. Algunas se solapan a propósito para mostrar conflictos de horario, por ejemplo `lis-tram-tour` con `lis-sintra-day` el 11 de junio.
- La lista completa está en [`provider_mock/fixtures.py`](provider_mock/fixtures.py).

**Identificador y precio.** Cada fixture se materializa según el tamaño del grupo: `offer-<fixture>-p<viajeros>`, por ejemplo `offer-lis-hotel-central-p2`. El precio cubre a todo el grupo y el tramo, la estancia o la actividad completos: `base + por_viajero × viajeros`, redondeado *half-up* a centavos. Por ejemplo, `lis-hotel-central` cuesta 480.00 + 95.00 × 2 = **670.00 USD** para 2 viajeros.

**Fixtures con comportamiento especial** (no hace falta activar nada):

| Oferta | Comportamiento |
| --- | --- |
| `lis-hotel-belem`, `lis-train-mad`, `par-cooking-class` | `available: false`, por lo que reservarlas da `409 OFFER_UNAVAILABLE` |
| `par-apartment-montmartre` | La reserva queda `pending` hasta que se resuelva con `/settle` |
| `lis-surf-lesson` | La reserva se acepta como `failed`, con `failure` |

## Reglas de búsqueda

- `destination` y `origin` se comparan sin distinguir mayúsculas. `origin` se ignora fuera de `transport`.
- Una oferta encaja si su fecha local de inicio y su fecha local de fin caen dentro de `[startsOn, endsOn]`, ambos incluidos, y si el grupo cabe (`travelers ≤ max_travelers`).
- Sin coincidencias se devuelve `200` con `partial=false` y `offers: []`. Un destino desconocido también da resultado vacío, no un error.
- Se devuelve `400` si `providerId` no existe o si existe pero no ofrece esa categoría.
- Las ofertas no disponibles **sí** aparecen, con `available: false`.
- Si un supplier falla y otro responde: `200`, `partial=true` y un warning por cada supplier fallido. Si fallan todos los consultados: `503`, con los warnings en `details`. La excepción es una consulta a un único supplier limitado por tasa, que devuelve `429` con `Retry-After`.
- Un quote expirado (cuando `ahora ≥ validUntil`) se renueva con nueva `revision` y nuevo `validUntil` en la búsqueda y en `GET /offers/{id}`. Mientras siga vigente, la revisión no cambia.

## Reglas de reserva

Orden de comprobaciones en `POST /bookings`:

1. Validación del cuerpo y del header `Idempotency-Key` (1-128 caracteres); si falla, `400`.
2. **Resolución de la clave** para ese caller. Con el mismo cuerpo, se responde `200` con el estado actual de la reserva, aunque el quote haya expirado o cambiado. Con otro cuerpo, `409 IDEMPOTENCY_CONFLICT`. La comparación es semántica: el orden de las propiedades JSON no importa.
3. `clientReference` ya usada por otra reserva aceptada del mismo caller: `409 REFERENCE_CONFLICT`.
4. Oferta inexistente: `404`.
5. Escenario del supplier: `503` o `429` sin crear nada.
6. `available=false`: `409 OFFER_UNAVAILABLE`. Esta comprobación va antes que la de los términos.
7. Quote expirado, revisión distinta o precio distinto: `409 OFFER_CHANGED`.
8. Aceptación: `201`. El resultado es `confirmed`, `pending` o `failed`, según la fixture o el escenario. Se persiste **antes** de responder.

Una petición rechazada no consume ni la clave ni la referencia. La reserva guarda una copia inmutable de la oferta y del precio aceptados. Las peticiones concurrentes idénticas crean exactamente una reserva.

## Escenarios de control (`/_control`, fuera del contrato)

Sin autenticación y con cuerpo JSON. Todos los ejemplos usan `B=http://localhost:4010`.

### `POST /_control/scenario`: fallos por supplier

```bash
curl -X POST $B/_control/scenario -H 'Content-Type: application/json' \
  -d '{"supplierId": "supplier-b", "mode": "unavailable"}'
```

| Campo | Valores | Defecto |
| --- | --- | --- |
| `supplierId` | `supplier-a` / `supplier-b` / `supplier-c` | (obligatorio) |
| `mode` | `normal`, `unavailable`, `timeout`, `rate_limited` | `normal` |
| `delaySeconds` | Retraso de las operaciones directas en `timeout` | `10` |
| `retryAfterSeconds` | Valor de `Retry-After` en `rate_limited` | `2` |
| `bookingOutcome` | `confirmed`, `pending`, `failed`: fuerza el resultado de las reservas nuevas | `null` (lo decide la fixture) |

El escenario afecta a **todas** las operaciones que tocan a ese supplier: búsqueda, `GET /offers/{id}`, reservas nuevas, replays y consultas de reservas.

| Modo | Búsqueda con varios suppliers | Búsqueda solo a ese supplier | Operaciones directas |
| --- | --- | --- | --- |
| `unavailable` | warning `PROVIDER_UNAVAILABLE`, `partial=true` | `503` | `503`, sin crear nada |
| `rate_limited` | warning `RATE_LIMITED`, `partial=true` | `429` + `Retry-After` | `429` + `Retry-After`, sin crear nada |
| `timeout` | espera `MOCK_SUPPLIER_DEADLINE_SECONDS` y da warning `PROVIDER_TIMEOUT` | `503` | responde bien, pero **tras `delaySeconds`** |

**Respuesta perdida (US-08).** En modo `timeout`, `POST /bookings` **crea y persiste la reserva y después** espera `delaySeconds` antes de responder. Un cliente con timeout menor no ve la respuesta, pero la reserva existe y se puede reconciliar con `GET /v1/bookings/by-reference/{ref}` o reintentando con la misma `Idempotency-Key`.

`GET /_control/scenario` lista los escenarios activos y `DELETE /_control/scenario` los borra todos.

### `/_control/clock`: reloj controlable

```bash
curl -X POST $B/_control/clock -H 'Content-Type: application/json' -d '{"now": "2030-06-01T12:00:00Z"}'  # congela
curl -X POST $B/_control/clock -H 'Content-Type: application/json' -d '{"advanceSeconds": 900}'       # expira los quotes
curl -X POST $B/_control/clock -H 'Content-Type: application/json' -d '{"now": "2030-06-01T12:00:00Z", "frozen": false}'  # desplaza y sigue corriendo
curl $B/_control/clock            # estado actual
curl -X DELETE $B/_control/clock  # vuelve a la hora del sistema
```

El reloj gobierna `validUntil`, `searchedAt`, `createdAt` y `updatedAt`. Por defecto se usa la hora del sistema; como las fixtures son de 2030, siguen siendo futuras.

### `POST /_control/booking/{id}/settle`: resolver una reserva pending

```bash
curl -X POST $B/_control/booking/booking-000001/settle -H 'Content-Type: application/json' -d '{"status": "confirmed"}'
curl -X POST $B/_control/booking/booking-000001/settle -H 'Content-Type: application/json' \
  -d '{"status": "failed", "failure": {"code": "NO_INVENTORY", "message": "Host declined."}}'
```

Solo se puede resolver una reserva en `pending`; si ya terminó, se devuelve `409 BOOKING_NOT_PENDING`, porque los resultados terminales no se revierten.

### `POST /_control/offers/{offerId}`: cambiar los términos de una oferta

Este control es un añadido mío para provocar `OFFER_CHANGED` y `OFFER_UNAVAILABLE` (US-07) en mitad de un flujo. Cualquier cambio genera una revisión nueva y un `validUntil` nuevo.

```bash
curl -X POST $B/_control/offers/offer-lis-hotel-central-p2 -H 'Content-Type: application/json' -d '{"price": "710.00"}'
curl -X POST $B/_control/offers/offer-lis-hotel-central-p2 -H 'Content-Type: application/json' -d '{"available": false}'
```

### `POST /_control/reset`

Borra reservas, idempotencia, referencias y quotes, y limpia escenarios y reloj. Es la "deliberate fixture reset" que menciona el contrato.

## Persistencia y límites

- Los datos (reservas, idempotencia, referencias y quotes) se escriben en un JSON de forma atómica (archivo temporal, `fsync` y `rename`) **antes de responder**, así que sobreviven a reinicios y a respuestas perdidas. Las claves de idempotencia no caducan; el contrato exige al menos 7 días.
- Los escenarios y el reloj **no** se persisten: un reinicio los limpia.
- El mock corre en **un solo proceso con un solo worker**. La garantía de "exactamente una reserva" ante peticiones concurrentes depende de eso; con varios workers no se cumpliría.
- No guarda tokens: cada caller se identifica por un hash del token.
