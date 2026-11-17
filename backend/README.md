# backend — Aplicación principal (FastAPI + SQLAlchemy async), monolito modular con puertos y adaptadores

## Estructura

- `app/domain`: entidades, reglas y casos de uso, sin frameworks.
- `app/ports`: protocolos (repositorios, reloj, proveedor).
- `app/adapters`: SQLAlchemy/Postgres, cliente HTTP del proveedor y reloj del sistema.
- `app/api`: HTTP.
- `migrations/` + `alembic.ini`: esquema y datos de prueba. El contenedor ejecuta `alembic upgrade head` antes de servir.

## Piezas

- **Identidad y permisos:** la identidad es de prueba, con el header `X-User-Id` (`ana`, `beto`, `carla`, `dani`). `require_role` (en `app/api/deps.py`) usa `load_access` del dominio. Un no participante recibe `404` (igual que un viaje inexistente) y un participante con rol insuficiente, `403`. Los casos de uso del dominio vuelven a comprobar el rol.
- **Proveedor:** puerto `app/ports/provider.py` (`ProviderClient`) y adaptador `app/adapters/provider_http.py`. El adaptador usa httpx con un plazo total por llamada (`PROVIDER_TIMEOUT_SECONDS`). Cada fallo se convierte en un `ProviderError` de dominio distinto (`app/domain/provider.py`). Cada llamada queda en el log con el `requestId` del proveedor y el de la petición.
- **Búsqueda (US-03):** `app/domain/search.py`. Responde `outcome` = `complete` | `partial` | `empty` | `failed`, con `failedSuppliers`, `searchedAt`, `receivedAt` y un `disclaimer`. Cada oferta incluye `priceType: "estimate"`, `validUntil`, `quoteExpired`, `available` y `restrictions`.
- **Itinerario y costes (US-04/05/06):**
  - `app/domain/planning.py`: mutaciones con versión e historial en `trip_changes`.
  - `app/domain/itinerary.py`: marcas y reglas (funciones puras).
  - `app/domain/costs.py`: desglose y redondeo (funciones puras).
- **Reservas (US-07/08, ADR 0003):**
  - `app/domain/bookings.py`: máquina de estados y `nextStep`.
  - `app/domain/reservations.py`: casos de uso y reconciliación.
  - `app/workers/`: worker de la tabla `jobs`, que se arranca con `python -m app.workers`.
  - La base de datos garantiza un intento activo por ítem y que los datos aceptados son inmutables (índice parcial y trigger, migración 0004).

## Endpoints

"Viewer" incluye a editores y organizadores, y "Editor" incluye al organizador. Las mutaciones marcadas con *versión* exigen la versión del viaje (ADR 0001).

| Método y ruta | Rol mínimo | Notas |
| --- | --- | --- |
| `GET /me`, `GET /users` | usuario de prueba | identidad actual; directorio de usuarios |
| `POST /trips` | usuario de prueba | quien crea el viaje es su organizador |
| `GET /trips` | usuario de prueba | solo los viajes en los que participa |
| `GET /trips/{id}` | Viewer | incluye `version` y `ETag` |
| `PATCH /trips/{id}` | Editor | *versión* en el cuerpo |
| `GET /trips/{id}/members` | Viewer | |
| `PUT /trips/{id}/members/{userId}` | Organizer | concede `editor` o `viewer` |
| `DELETE /trips/{id}/members/{userId}` | Organizer | |
| `POST /trips/{id}/offers/search` | Viewer | US-03 |
| `GET /trips/{id}/itinerary` | Viewer | marcas, información que falta y reglas |
| `POST /trips/{id}/itinerary/items` | Editor | *versión*; `offerId` (precio del proveedor) |
| `DELETE /trips/{id}/itinerary/items/{itemId}?version=` | Editor | *versión* en la query |
| `PUT /trips/{id}/itinerary/order` | Editor | *versión*; `itemIds` |
| `POST /trips/{id}/itinerary/items/{itemId}/refresh` | Editor | *versión*; actualiza la cotización |
| `GET /trips/{id}/costs` | Viewer | US-05 |
| `POST /trips/{id}/itinerary/items/{itemId}/bookings` | Editor | US-07; `expectedRevision` y `expectedPrice`. `201` registra un intento (ver `status`); `409` si los términos cambiaron, sin enviar nada |
| `POST /trips/{id}/bookings/{bookingId}/retry` | Editor | solo `draft`; misma `Idempotency-Key` |
| `GET /trips/{id}/bookings` | Viewer | estado, `booked`, `lastError`, `nextStep`, oferta y precio aceptados |
| `GET /trips/{id}/bookings/{bookingId}` | Viewer | |
| `GET /healthz`, `GET /readyz` | — | vida del proceso; base de datos accesible |

OpenAPI interactivo en `/docs`.
