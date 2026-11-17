# backend — Aplicación principal (FastAPI + SQLAlchemy async), monolito modular con puertos y adaptadores

- `app/domain`: entidades, reglas y casos de uso (sin frameworks). `app/ports`: protocolos (repositorios, reloj). `app/adapters`: SQLAlchemy/Postgres, reloj del sistema. `app/api`: HTTP.
- `migrations/` + `alembic.ini`: esquema (`0001`) y usuarios de prueba (`0002`). El contenedor ejecuta `alembic upgrade head` antes de servir.
- Identidad de prueba: header `X-User-Id` (`ana`, `beto`, `carla`, `dani`). Autorización: `require_role` (en `app/api/deps.py`) usa `load_access` del dominio. Un no participante recibe `404` y un rol insuficiente, `403`.
- Proveedor: puerto `app/ports/provider.py` (`ProviderClient`), adaptador `app/adapters/provider_http.py` (httpx, con un plazo total por llamada vía `PROVIDER_TIMEOUT_SECONDS`). Cada fallo se convierte en un `ProviderError` de dominio distinto (`app/domain/provider.py`), y cada llamada se registra en el log con el `requestId` del proveedor y el de la petición.
- Búsqueda (US-03): `POST /trips/{id}/offers/search`, para cualquier participante. Responde `outcome` = `complete` | `partial` | `empty` | `failed`, con `failedSuppliers`, `searchedAt`, `receivedAt` y un `disclaimer`. Cada oferta incluye `priceType: "estimate"`, `validUntil`, `quoteExpired`, `available` y `restrictions`.
- Endpoints actuales: `GET /me`, `GET /users`, `POST/GET /trips`, `GET/PATCH /trips/{id}`, `GET /trips/{id}/members`, `PUT/DELETE /trips/{id}/members/{userId}`, `POST /trips/{id}/offers/search`, `/healthz`, `/readyz`. OpenAPI en `/docs`.
