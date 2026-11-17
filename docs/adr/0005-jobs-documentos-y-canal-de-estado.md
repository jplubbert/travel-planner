# 0005. Jobs en PostgreSQL, documento como copia inmutable y canal de estado

- **Estado:** Aceptado
- **Fecha:** 2026-11-17
- **Historias relacionadas:** US-08, US-09, US-10, US-02 (suscripciones), y la recuperación tras desconexión del apartado "Cross-cutting behavior"

## Contexto

- **US-09:** generar el documento sin bloquear al usuario, con estados visibles (`waiting`, `in progress`, `complete`, `failed`) que se recuperan al recargar o reconectar. Ni las interrupciones ni las peticiones repetidas pueden dejar un archivo ausente o incompleto marcado como listo, y debe haber forma de reintentar.
- **US-10:** el documento identifica qué información y qué momento representa; las ediciones posteriores no lo alteran; solo los participantes lo descargan. Se deben detectar o corregir los desajustes entre archivos y referencias.
- **US-08 (ADR 0003):** necesita trabajo en segundo plano reanudable: reconciliación y consulta de reservas pendientes.
- **Transversal:** recuperar el estado tras una desconexión sin depender de que llegue cada notificación. Los permisos se aplican también a las suscripciones de estado (US-02).

Hay que decidir tres cosas: dónde viven los trabajos en segundo plano, qué es un documento, y cómo se entera el cliente de los cambios.

## Decisión 1: los jobs viven en PostgreSQL (tabla `jobs`)

**Opciones evaluadas**

- **Tareas en memoria del proceso (`asyncio.create_task`, `BackgroundTasks`).** Se pierden al reiniciar: una reconciliación o una generación pendiente desaparecen sin rastro. Descartada.
- **Broker externo (Redis + RQ/Celery/arq).** Sirve, pero añade un servicio y un segundo almacén de estado. El job y el cambio de negocio (crear el documento, pasar una reserva a `unknown`) no se pueden confirmar en la misma transacción, y aparece la ventana "guardé el documento pero no encolé el job". No hay volumen que lo justifique.
- **Tabla `jobs` en la misma base de datos (elegida).**

**Cómo funciona**

- El job se inserta **en la misma transacción** que el cambio que lo motiva, así que no puede haber uno sin el otro.
- Los workers reclaman con `SELECT … FOR UPDATE SKIP LOCKED` y una lease (`locked_until`). Varios workers pueden correr a la vez sin procesar el mismo job, y un job cuyo worker murió vuelve a estar disponible cuando vence su lease.
- `dedupe_key` es único solo entre jobs activos, lo que evita duplicados sin bloquear trabajo futuro.
- **Comportamiento por tipo:**
  - `generate_document`: reintentos con backoff y, tras `DOCUMENT_MAX_ATTEMPTS`, el documento pasa a `failed`; se puede reintentar de forma explícita.
  - `sync_booking`: no se rinde nunca (ADR 0003).
- **Coste:** sondeo periódico de la tabla (`WORKER_POLL_INTERVAL_SECONDS`) y carga en la base de datos principal. Es aceptable a esta escala; un cambio futuro a `LISTEN/NOTIFY` o a un broker no cambiaría el contrato de los jobs.

## Decisión 2: un documento es una copia inmutable, renderizada después

**Opciones evaluadas**

- **Renderizar en el momento con los datos vivos.** Bloquea la petición y, si se genera en segundo plano a partir de datos vivos, el documento refleja lo que había *cuando el worker llegó*, no cuando se pidió. Además, regenerarlo tras perder el archivo daría otro contenido. Descartada.
- **Guardar solo el archivo, sin copia de los datos.** Si el archivo se pierde o se corrompe, no hay de dónde regenerarlo fielmente. Descartada.
- **Guardar el archivo dentro de la base de datos (`bytea`).** La consistencia es trivial, pero carga la base con binarios y mezcla responsabilidades. El volumen de disco local sirve igual detrás del puerto `DocumentStorage`.
- **Copia inmutable en la fila del documento, renderizada a un archivo después (elegida).**

**Cómo funciona**

- **Al pedirlo (`POST /trips/{id}/documents`, editor, `202`):** en una transacción, con la fila del viaje bloqueada, se toma la copia (`snapshot`: viaje, itinerario con sus marcas, costes y estados de reserva) con la versión del viaje y la hora. En esa misma transacción se crean el documento en `waiting` y su job.
- **Peticiones repetidas:**
  - Mientras haya un documento en curso para la misma versión, se devuelve ese. Lo garantiza un índice único parcial: dos peticiones simultáneas comparten documento.
  - Si ya hay uno `complete` con el mismo contenido (`snapshot_sha256`, sin contar la hora), se reutiliza.
- **El worker:**
  1. pasa el documento a `in_progress`;
  2. lo renderiza **desde la copia** a HTML autocontenido y determinista;
  3. lo escribe en un archivo temporal, hace `fsync`, lo renombra de forma atómica y hace `fsync` del directorio;
  4. **solo entonces** lo marca `complete` con sha256 y tamaño.

  Si se interrumpe en cualquier punto, el documento queda `waiting` o `in_progress`, nunca `complete`, y la lease devuelve el job a otro worker.
- **Inmutabilidad:** un trigger impide modificar la copia, la versión, el viaje y el solicitante. Las ediciones posteriores generan documentos nuevos.
- **Descarga (cualquier participante):**
  - se verifican el sha256 y el tamaño antes de entregar;
  - si el archivo falta o no coincide, **no se entrega nada**, se registra el problema y el documento vuelve a `waiting` para regenerarse desde la copia; como el render es determinista, sale el mismo hash;
  - un barrido periódico borra los archivos sin referencia y los temporales viejos, y reencola los documentos completos cuyo archivo desapareció.

## Decisión 3: canal de estado por SSE, solo como aviso

**Opciones evaluadas**

- **Solo consultas periódicas (polling) desde el cliente.** Es lo más robusto, pero da latencia o carga innecesaria, y peor experiencia para estados que cambian solos (reservas `pending`, documentos).
- **WebSockets.** Bidireccional, que aquí no hace falta (el cliente no envía nada por el canal). Exige gestionar protocolo, reconexión y autenticación propios, y no aporta frente a SSE.
- **SSE con `LISTEN/NOTIFY` de Postgres.** Menos consultas, pero los avisos se pierden si nadie escucha, y hay que mantener una conexión dedicada por proceso. Es una optimización posible más adelante.
- **SSE por viaje, con sondeo en el servidor (elegida).**

**Cómo funciona**

- `GET /trips/{id}/events` (cualquier participante; un no participante recibe `404`). Cada `EVENTS_POLL_SECONDS`, el stream compara una huella del estado del viaje (versión, estado y `updated_at` de reservas y documentos) y emite `trip`, `booking` o `document` con el id y el nuevo estado. Envía `keep-alive` y `retry: 3000`.
- **En cada sondeo se vuelve a comprobar el acceso:** si el usuario deja de participar, recibe `access-revoked` y el stream se cierra.
- **Los eventos son avisos, no la fuente de verdad.** No hay repetición de eventos: al (re)conectar llega `ready` y el cliente **siempre** relee con `GET`. Un evento perdido o duplicado no puede dejar al cliente con un estado incorrecto.

## Consecuencias

- **Positivas:**
  - un solo almacén transaccional para el estado de negocio y los trabajos;
  - documentos reproducibles y verificables;
  - nada se presenta como listo sin un archivo comprobado;
  - el canal de estado respeta los mismos permisos que el resto de la API.
- **Negativas / riesgos:**
  - sondeo de la tabla `jobs` y un sondeo por cada stream SSE abierto, que es carga lineal con el número de clientes conectados;
  - el disco local exige un volumen compartido entre `api` y `worker`, y en un despliegue con varias máquinas habría que cambiar de adaptador (objeto en S3 o similar) detrás del mismo puerto;
  - si el renderizador cambia, regenerar un documento antiguo produce otro hash; la copia sigue siendo la fuente de verdad del contenido.

## Verificación

- `tests/integration/test_us09_us10_documents.py`:
  - `202` inmediato y worker que completa el documento;
  - contenido (itinerario, costes, estados de reserva, versión y hora);
  - peticiones repetidas o simultáneas que comparten documento;
  - worker matado escribiendo o justo después de escribir, recuperado por lease;
  - copia tomada al pedir y no al generar;
  - ediciones posteriores que no alteran el documento;
  - archivo borrado, corrupto o truncado que no se entrega y se regenera con el mismo hash;
  - barrido de huérfanos;
  - permisos.
- `tests/integration/test_status_channel.py`: avisos de documento, reserva y viaje; reconexión sin repetición más `GET`; cierre al perder el acceso; `404` y `401`.
- `tests/integration/test_worker_and_integrity.py`: reparto de jobs entre workers, lease y deduplicación.
- `tests/unit/test_local_storage.py`: un fallo a mitad de escritura deja intacto el archivo anterior.
