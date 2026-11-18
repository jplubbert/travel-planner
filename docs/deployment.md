# Propuesta de despliegue

Es una propuesta: **no se ha desplegado**. Lo único ejecutado es Docker Compose en local (`docker compose up --build` y `scripts/e2e.sh`).

## Servicios

Las mismas imágenes que en Compose:

| Servicio | Imagen | Réplicas | Notas |
| --- | --- | --- | --- |
| `frontend` | `frontend/Dockerfile` (build estático + nginx) | 2+ detrás del balanceador | Sin estado. En producción el bundle podría servirse desde un CDN, pero hay que mantener el proxy de `/api` (mismo origen) o configurar CORS |
| `api` | `backend/Dockerfile` | 2+ | Sin estado en memoria: todo el estado está en PostgreSQL y el almacenamiento. Health: `/healthz` (vida) y `/readyz` (base de datos accesible) |
| `worker` | `backend/Dockerfile`, `python -m app.workers` | 1+ (escalable) | Ver "Escalado del worker" |
| migraciones | `backend/Dockerfile`, `alembic upgrade head` | job previo a cada despliegue | Hoy las ejecuta el contenedor `api` al arrancar. Con varias réplicas conviene un job único previo (ver abajo) |
| proveedor | el real, no el mock | — | `PROVIDER_BASE_URL` y `PROVIDER_TOKEN`. El mock no se despliega en producción |

**Orden de despliegue**

1. Job de migraciones.
2. `api`, con *rolling update* que espera a `/readyz`.
3. `worker`.
4. `frontend`.

Las migraciones deben ser compatibles con la versión anterior durante el *rolling update*: primero se añade, y solo se borra en un despliegue posterior.

**Parada ordenada**

- **Worker:** ante `SIGTERM` termina el job en curso y sale (`app/workers/__main__.py`). Si muere antes, la lease vence (`WORKER_JOB_LEASE_SECONDS`) y otro worker retoma el job.
- **API:** al cerrar, el lifespan detiene el gestor de sesiones MCP, cierra el cliente HTTP del proveedor y libera el pool de conexiones. Las peticiones en vuelo las drena uvicorn. Si una reserva queda a medias, se queda en `submitted` y el worker la reconcilia (ensayado en la demo).

## Base de datos

- PostgreSQL gestionado (16+), con copias automáticas y recuperación a un punto en el tiempo.
- Pool de conexiones dimensionado por réplica. Cada stream SSE abierto ocupa conexiones al sondear, así que también cuenta.
- Retención: el historial de reservas, documentos y `trip_changes` no se borra. Hace falta una política de archivado si crece.

## Almacenamiento de documentos

- **Hoy:** un volumen local (`DOCUMENT_STORAGE_DIR`) compartido por la API y el worker, con escritura atómica (temporal, `fsync` y `rename`). Esto exige que ambos monten el mismo volumen, lo que no escala entre máquinas.
- **Producción:** un adaptador S3 (o compatible) del puerto `DocumentStorage` (`app/ports/documents.py`). Así:
  - la clave del objeto sigue siendo `trip/document.html`;
  - la subida de un objeto completo ya es atómica, así que no hacen falta temporal ni `rename`;
  - el sha256 se sigue verificando en cada descarga;
  - el barrido de huérfanos lista el bucket;
  - la descarga puede seguir pasando por la API, que comprueba permisos y hash, o usar URLs prefirmadas de vida corta emitidas **después** de comprobar permisos y hash.
- El dominio no cambia: solo el adaptador.

## Secretos y configuración

Toda la configuración se lee del entorno (`backend/app/config.py`, documentada en `.env.example`).

- Los secretos se inyectan desde un gestor de secretos (por ejemplo, el de la nube elegida o Vault), nunca desde el repositorio:
  - `DATABASE_URL`;
  - `PROVIDER_TOKEN`;
  - `MCP_TOKEN_SECRET`: obligatorio cambiarlo, porque el valor por defecto es solo para desarrollo;
  - `GEMINI_API_KEY`.
- `GEMINI_API_KEY` y `MCP_TOKEN_SECRET` son `SecretStr`: no aparecen en `repr`, en logs ni en tracebacks.
- Hay que rotar `MCP_TOKEN_SECRET` sin cortar sesiones. Los tokens duran `MCP_TOKEN_TTL_SECONDS` (300 s), así que basta con aceptar dos secretos durante una ventana. **No implementado:** hoy hay un solo secreto.

## Escalado del worker

- Los jobs se reclaman con `FOR UPDATE SKIP LOCKED` y lease, así que varias réplicas no se pisan (`tests/integration/test_worker_and_integrity.py` lo prueba con dos workers). Se escala por profundidad de la cola: jobs `queued` con `run_after <= now()`.
- La deduplicación (clave única solo entre jobs activos) evita encolar dos veces la misma reconciliación o generación.
- Los límites de tasa del proveedor son globales. Más workers no significa más reconciliaciones por segundo si el proveedor responde `429`.
- Hoy la reconciliación usa un backoff exponencial (`BOOKING_RETRY_BASE_SECONDS` hasta `BOOKING_RETRY_MAX_SECONDS`) y **no usa `Retry-After`** para programar el siguiente intento: solo lo guarda en `lastError`. Tampoco hay un limitador compartido entre réplicas. Ambas cosas están **pendientes para producción**.

## Observabilidad

**Lo que existe**

- Cada petición lleva `X-Request-Id`, que se acepta o se genera, aparece en los logs y se devuelve en los errores (`requestId`).
- Cada llamada al proveedor registra la operación, el estado, la duración, el `requestId` del proveedor y el de la petición, nunca el token.
- Las reservas guardan `lastError` (código, mensaje, `providerRequestId`, `retryAfterSeconds`, hora) y los documentos guardan `error`.
- El worker registra cada job con su resultado e intento.

**Qué añadir**

- Logs en JSON hacia el agregador de la plataforma.
- Métricas:
  - latencia y errores por endpoint y por operación del proveedor;
  - profundidad y edad de la cola de jobs;
  - reservas en `unknown` o `submitted` más allá de su plazo;
  - documentos `failed`;
  - streams SSE abiertos;
  - llamadas al modelo y tokens consumidos.
- Alertas:
  - reservas sin resolver más de N minutos;
  - cola de jobs creciendo;
  - tasa de `5xx`;
  - `/readyz` fallando.
- Trazas (OpenTelemetry) de la API al proveedor y al worker, propagando el `requestId` en los jobs. Hoy el worker registra `request_id=None` porque el job no lo hereda.

## Qué cambiaría para producción

| Hoy | Producción | Motivo |
| --- | --- | --- |
| Identidad de prueba en `X-User-Id` | OIDC (por ejemplo, Auth0, Cognito o Keycloak): la API valida el JWT y obtiene el usuario del `sub`. `load_access` no cambia | Cualquiera puede enviar una cabecera; el PRD permite identidades de prueba solo para la entrega |
| Tokens MCP emitidos solo para el asistente | Si hay clientes MCP externos, un endpoint autenticado que emita tokens de corta vida (o OAuth según la especificación MCP) | Hoy no hace falta (ADR 0006) |
| Documentos en un volumen local | S3 o compatible, a través del puerto `DocumentStorage` | Varias máquinas, durabilidad, coste |
| SSE que sondea PostgreSQL cada segundo por stream | `LISTEN/NOTIFY`: los casos de uso emiten `NOTIFY trip_<id>` al confirmar, y cada réplica mantiene una conexión `LISTEN` y reparte los avisos a sus streams. Se conserva el `ready` y la relectura con `GET`, y un sondeo lento como respaldo | El sondeo escala linealmente con las conexiones; los avisos siguen siendo solo pistas |
| Migraciones al arrancar la API | Job único previo al despliegue | Evita carreras entre réplicas |
| Asistente en el proceso de la API | Mantenerlo, con límites de concurrencia por usuario, o separarlo si su carga compite con la API | Hoy comparte pool de conexiones (ADR 0006) |
| Mock del proveedor | Proveedor real | El mock solo existe para desarrollo, tests y demo |

## Coste

No estimado, más allá del modelo: las evaluaciones con modelo cuestan unos 0,10–0,20 USD por pasada, una estimación no medida (`evals/README.md`).
