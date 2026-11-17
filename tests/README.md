# tests — Pruebas del backend

- `unit/test_provider_http.py`: mapeo de cada código de estado y fallo de transporte a su error de dominio, plazo total, logs con `requestId` y sin token.
- `unit/`: dominio puro (roles, máquina de estados de reservas, documentos, jobs, dinero) y reglas de arquitectura (el núcleo no importa frameworks; la app nunca llama a `/_control`). No necesitan servicios.
- `integration/`: API y repositorios contra **PostgreSQL real**. La sesión recrea la base `TEST_DATABASE_URL` (por defecto `travel_test`) y la migra con Alembic (upgrade, downgrade completo y upgrade de nuevo); cada test empieza con las tablas de datos vacías. Requiere `docker compose up -d db`.
  - `test_us01_trips.py`, `test_us02_sharing.py`, `test_us03_search.py`, `test_us04_itinerary.py`, `test_us05_costs.py`, `test_us06_concurrency.py`, `test_us07_us08_bookings.py`: tests de aceptación por historia.
  - `test_worker_and_integrity.py`: la cola de jobs (dos workers nunca reclaman el mismo job, reclamación por lease expirada, deduplicación) y la integridad de reservas en la base de datos (trigger de inmutabilidad, un intento activo por ítem).
  - Los fallos de US-07/08 que el mock no puede provocar solo (proceso que muere antes o después de enviar, términos que cambian justo antes del `POST`) se simulan envolviendo el cliente HTTP real (`WrappedProvider`).
  - El mock del proveedor se arranca **como proceso HTTP real** (uvicorn en un puerto libre) una vez por sesión. Los escenarios de fallo se activan con su `/_control` **solo desde los tests** (fixture `mock_control`, que además resetea el mock).
  - `test_provider_client.py`: cada operación del puerto `ProviderClient` contra el mock real, incluida la respuesta perdida.
  - `test_persistence.py`: migraciones == modelos, rollback de trabajo no confirmado, restricciones de la BD, round-trip de cada repositorio.

Los tests del mock del proveedor están en `mock/tests/`.
