# tests — Pruebas del backend

- `unit/`: dominio puro (roles, máquina de estados de reservas, documentos, jobs, dinero) y reglas de arquitectura (el núcleo no importa frameworks; la app nunca llama a `/_control`). No necesitan servicios.
- `integration/`: API y repositorios contra **PostgreSQL real**. La sesión recrea la base `TEST_DATABASE_URL` (por defecto `travel_test`) y la migra con Alembic (upgrade, downgrade completo y upgrade de nuevo); cada test empieza con las tablas de datos vacías. Requiere `docker compose up -d db`.
  - `test_us01_trips.py`, `test_us02_sharing.py`: tests de aceptación por historia.
  - `test_persistence.py`: migraciones == modelos, rollback de trabajo no confirmado, restricciones de la BD, round-trip de cada repositorio.

Los tests del mock del proveedor están en `mock/tests/`.
