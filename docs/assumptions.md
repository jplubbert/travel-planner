# Supuestos y preguntas abiertas

Registro de ambigüedades del PRD (`assignment/prd.md`), el candidate guide y el contrato del proveedor (`assignment/openAPI.json`), y cómo se resolvieron. Formato de cada entrada:

> **Supuesto:** … — **Afecta a:** US-XX / sección — **Motivo:** … — **Impacto si es incorrecto:** …

## Generales

## Identidad y permisos (US-02, US-12)

> **Supuesto:** La identidad es de prueba: el header `X-User-Id` nombra a uno de los usuarios precargados por la migración `0002` (`ana`, `beto`, `carla`, `dani`). Sin header, o con un usuario desconocido, se responde `401`. — **Afecta a:** todas las historias — **Motivo:** el PRD permite identidades de prueba y no exige un sistema de identidad. — **Impacto:** en producción se sustituiría por autenticación real sin tocar el dominio, porque la autorización ya trabaja con `user_id`.

> **Supuesto:** Cada viaje tiene exactamente un organizador, su creador. El organizador solo puede conceder los roles `editor` y `viewer`, y su propio rol no se puede cambiar ni quitar (`409 ORGANIZER_ROLE_FIXED`). — **Afecta a:** US-02 — **Motivo:** el PRD describe al organizador como quien crea el viaje y gestiona a los participantes; transferir la organización queda fuera de alcance.

> **Supuesto:** Los editores pueden cambiar los datos del viaje (nombre, destinos, fechas, viajeros y presupuesto), además del itinerario. Solo el organizador gestiona a los participantes. — **Afecta a:** US-02 — **Motivo:** el PRD dice "editors can perform planning actions; viewers cannot change the trip", y fechas y presupuesto forman parte de la planificación.

> **Supuesto:** Un no participante recibe `404 TRIP_NOT_FOUND` en todas las operaciones del viaje, con la misma respuesta que para un viaje inexistente o un id mal formado; así no se revela que el viaje existe. Un participante sin el rol necesario recibe `403`. — **Afecta a:** US-02, US-12.

> **Supuesto:** Los cambios de participantes no usan control de versión: `PUT` fija el rol (es idempotente) y solo el organizador, que es único, puede hacerlos. — **Afecta a:** US-02/US-06 — **Impacto:** dos pestañas del mismo organizador cambiando el rol de la misma persona dejan el último valor; no se pierde ninguna edición de otro participante.

## Viajes e itinerario (US-01, US-04, US-06)

> **Supuesto:** Además de destinos, fechas y presupuesto, un viaje registra el número de viajeros (de 1 a 20, el rango del contrato del proveedor). — **Afecta a:** US-01, US-03 — **Motivo:** la búsqueda en el proveedor exige `travelers`, y el precio de cada oferta cubre a todo el grupo. Los participantes de la aplicación no tienen por qué viajar todos (un viewer puede no viajar).

> **Supuesto:** Las fechas del viaje no pueden empezar antes de hoy (UTC). Esto se comprueba solo al crear el viaje o al cambiar sus fechas, de modo que un viaje ya empezado se puede seguir editando. — **Afecta a:** US-01.

> **Supuesto:** El presupuesto se envía como cadena decimal con hasta 2 decimales (`"1500"`, `"1500.5"`, `"1500.00"`) y se guarda como `numeric(12,2)`. Los números JSON se rechazan para evitar errores de coma flotante. — **Afecta a:** US-01, US-05.

> **Supuesto:** Los errores de entrada devuelven `422 VALIDATION_ERROR` con `details.fields[]` (campo y mensaje) y se informan todos a la vez. Una edición sin `version` devuelve `400 VERSION_REQUIRED` (ADR 0001); la versión se envía en el cuerpo (`"version": n`) y `GET` también la expone en `ETag`. — **Afecta a:** US-01, US-06.

### Reglas simplificadas del itinerario (US-04)

La API devuelve estas reglas en `rules` en cada lectura del itinerario (fuente: `RULES` en `backend/app/domain/itinerary.py`):

1. Los horarios se comparan como instantes absolutos, usando el offset UTC de cada oferta.
2. Dos ítems de transporte o actividad entran en conflicto si sus intervalos se solapan. Que uno termine justo cuando empieza el siguiente no es conflicto.
3. El alojamiento solo entra en conflicto con otro alojamiento (un sitio por noche). Las actividades y el transporte durante una estancia son lo esperado.
4. No se consideran tiempos de traslado, conexiones, ventanas de check-in ni horarios de apertura.
5. Un ítem cae fuera del viaje si su fecha local de inicio es anterior al primer día del viaje o su fecha local de fin es posterior al último.
6. Cada noche, desde el primer día hasta la víspera del último, debería tener alojamiento. Las noches sin cubrir y los destinos sin alojamiento se informan en `missing`.
7. Un precio se marca como expirado cuando pasa el `validUntil` de su cotización; hay que actualizar el ítem para obtener los términos vigentes. Los ítems con reserva confirmada conservan el precio aceptado.
8. La disponibilidad y el precio reflejan la última cotización (`quotedAt`) y pueden haber cambiado desde entonces.

> **Supuesto:** No se puede añadir una oferta no disponible (`409 OFFER_UNAVAILABLE`), ni una cotizada para otro número de viajeros (`422`), ni la misma oferta dos veces en un viaje (`409`). Una oferta puede quedar no disponible o fuera de fechas **después** de añadirla, y entonces se marca en lugar de eliminarse. — **Afecta a:** US-04.

> **Supuesto:** No se puede quitar un ítem con una reserva activa (`submitted`, `unknown`, `pending` o `confirmed`): devuelve `409 ITEM_HAS_BOOKING`, porque la cancelación está fuera de alcance y quitarlo ocultaría un gasto real. Sí se puede quitar si sus reservas son `draft`, `failed` o `rejected`; esas reservas se conservan con `itinerary_item_id = NULL`. — **Afecta a:** US-04, US-05, US-08.

> **Supuesto:** Toda mutación del itinerario (añadir, quitar, reordenar, actualizar cotización) y la edición del viaje comparten **una sola versión por viaje** (ADR 0001). `DELETE` recibe la versión como parámetro `?version=`; el resto, en el cuerpo. Cada versión aceptada deja una entrada en `trip_changes` (quién, cuándo, qué), que es lo que el `409` lista en `changesSinceYourVersion`. — **Afecta a:** US-06.

## Búsqueda y proveedor (US-03, contrato OpenAPI)

> **Supuesto:** "Las opciones encajan con el grupo" significa que cada oferta se cotiza para el tamaño exacto del grupo pedido. El mock materializa cada fixture por número de viajeros (`offer-<fixture>-p<n>`) y excluye las que superan su capacidad. — **Afecta a:** contrato `searchOffers`, US-03, US-05 — **Motivo:** el contrato dice que el precio cubre a todo el grupo y que las opciones deben encajar en él. — **Impacto si es incorrecto:** bajo; solo cambia cómo se generan los IDs en el mock.

> **Supuesto:** Una oferta encaja en la ventana si su fecha local de inicio y su fecha local de fin están dentro de `[startsOn, endsOn]`. En transporte, la salida usa el offset del origen y la llegada el del destino. — **Afecta a:** `searchOffers` — **Motivo:** el contrato habla de una ventana "destination-local" pero no precisa qué extremos deben caer dentro. — **Impacto si es incorrecto:** un vuelo nocturno que llega al día siguiente del final de la ventana quedaría excluido.

> **Supuesto:** Un `providerId` que existe pero no ofrece la categoría pedida devuelve `400`, igual que uno desconocido. Un destino sin fixtures devuelve `200` vacío y completo. — **Afecta a:** `searchOffers` — **Motivo:** el contrato solo define el caso de supplier desconocido. — **Impacto si es incorrecto:** la app debe tratar ambos como error de petición, no como fallo del proveedor.

> **Supuesto:** Si todos los suppliers consultados fallan, se responde `503` aunque todos estuvieran limitados por tasa. Solo una consulta a un único supplier limitado da `429`. — **Afecta a:** `searchOffers` — **Motivo:** es la lectura literal de "return 503 or 429 for a rate-limited single-supplier query".

> **Supuesto:** Un escenario de supplier también afecta a los replays y a las consultas de reservas de ese supplier, no solo a la búsqueda y a las reservas nuevas. — **Afecta a:** reconciliación (US-08) — **Motivo:** así se puede demostrar que una reconciliación que falla deja el estado como desconocido en lugar de inventarlo.

> **Supuesto:** La búsqueda de la aplicación siempre usa el número de viajeros del viaje; no se puede cambiar en la petición. Las fechas de búsqueda, por defecto las del viaje, deben caer dentro de ellas. El destino es libre, para poder buscar también la vuelta a casa, por ejemplo con destino London. — **Afecta a:** US-03 — **Motivo:** las ofertas deben encajar con el grupo y el viaje.

> **Supuesto:** Una búsqueda fallida responde con el mismo cuerpo que las demás (`outcome: "failed"`), con `error` y los suppliers fallidos, y con un código HTTP de error: `504` por timeout, `502` si la respuesta del proveedor es inválida o el transporte falla, y `503` en el resto de casos (no disponible, límite de tasa con `Retry-After`, sin conexión o credenciales rechazadas). — **Afecta a:** US-03.

> **Supuesto:** Si una búsqueda termina parcial y sin ofertas (unos suppliers fallan y los que responden no encuentran nada), se clasifica como `partial`, no como `empty`, porque no se sabe si los suppliers caídos tenían opciones. — **Afecta a:** US-03.

> **Supuesto:** Los resultados de búsqueda no se cachean todavía; cada búsqueda consulta al proveedor. `quoteExpired` se calcula con el reloj de la aplicación frente al `validUntil` del proveedor. — **Afecta a:** US-03 — **Impacto:** más llamadas al proveedor; la reutilización de resultados se valorará con US-04/US-07.

## Costes y redondeo (US-05)

> **Reglas de redondeo** (también se devuelven en `rounding` en `GET /trips/{id}/costs`): los precios del proveedor y los aceptados ya son centavos exactos, y se suman con `Decimal`, sin coma flotante y sin redondeos intermedios. Cada importe devuelto se normaliza a 2 decimales con *half-up*, lo que no cambia nada si ya son centavos. `usedPercent = total / presupuesto × 100` se redondea *half-up* a 1 decimal, y se omite si el presupuesto es 0. Hay tests en `tests/unit/test_costs.py` (0.10 + 0.20 + 0.30 = 0.60 exacto; 6.25 % → 6.3 %).

> **Supuesto:** El coste de un ítem es `confirmed` solo si tiene una reserva `confirmed`; entonces se usa su `accepted_price`. Con una reserva `pending`, `unknown` o `submitted` sigue siendo `estimate`, con una nota del estado. Con `failed` o `rejected` vuelve a ser la estimación de la cotización. El total suma estimaciones y confirmados, que se muestran también por separado. — **Afecta a:** US-05.

> **Supuesto:** Los precios del mock se calculan como `base + por_viajero × viajeros` con `Decimal`, redondeando *half-up* a centavos. La app nunca recalcula un precio del proveedor; solo suma importes ya redondeados. — **Afecta a:** US-05.

## Reservas (US-07, US-08)

> **Supuesto:** Si una oferta no está disponible, el proveedor responde `OFFER_UNAVAILABLE` antes de comparar revisión y precio, aunque la revisión también haya cambiado. — **Afecta a:** `createBooking`, US-07 — **Motivo:** el contrato no fija la precedencia, y "no disponible" es la información más útil para el usuario.

> **Supuesto:** Una petición de reserva rechazada (4xx/5xx) no registra la `Idempotency-Key` ni la `clientReference`; solo las reservas aceptadas lo hacen. — **Afecta a:** `createBooking`, US-07/US-08 — **Motivo:** el contrato dice que el rechazo "does not create a booking" y que las claves se resuelven contra requests aceptados. — **Impacto si es incorrecto:** tras un `OFFER_CHANGED` la app podría reutilizar la clave con nuevos términos; aun así, la app usará una clave nueva por cada intento con términos distintos.

> **Supuesto:** `clientReference` es única por caller durante toda la vida del dataset del mock. — **Afecta a:** `createBooking` — **Motivo:** el schema la define como "unique within the caller's dataset".

> **Supuesto:** Reservar no exige la versión del viaje (ADR 0001): no modifica el viaje ni el itinerario, y su concurrencia la protege el índice de un intento activo por ítem. Sí exige los términos revisados (`expectedRevision`, `expectedPrice`). — **Afecta a:** US-06, US-07.

> **Supuesto:** Si al reservar la oferta ya está cotizada para otro número de viajeros (porque cambió el grupo del viaje), se responde `409 PARTY_SIZE_MISMATCH` sin enviar nada. — **Afecta a:** US-07.

> **Supuesto:** Un `201` al reservar significa que **se registró un intento**, no que se reservó. El estado (`confirmed`, `pending`, `unknown`, `draft`, `failed` o `rejected`), `booked` (verdadero solo con `confirmed`) y `nextStep` dicen qué pasó. Los rechazos detectados antes de enviar no crean intento y responden `409`. — **Afecta a:** US-07, US-08.

> **Supuesto:** La reconciliación no se rinde: un job `sync_booking` se reprograma con backoff (hasta `BOOKING_RETRY_MAX_SECONDS`) mientras la reserva no llegue a un estado terminal. Un proveedor caído deja la reserva en `unknown` con `lastError`, nunca en un éxito supuesto. — **Afecta a:** US-08.

> **Supuesto:** La cancelación y el reembolso están fuera de alcance (PRD). Una reserva `confirmed` impide quitar su ítem del itinerario. — **Afecta a:** US-04, US-08.

## Documentos (US-09, US-10)

> **Supuesto:** El formato es un **HTML autocontenido** (CSS en línea, sin scripts ni recursos externos), que se descarga como adjunto y se puede imprimir a PDF desde el navegador. — **Afecta a:** US-09 — **Motivo:** el PRD deja libre el formato; HTML no necesita dependencias, es determinista y se verifica por hash.

> **Supuesto:** Pedir un documento es una acción de editor (el PRD: los viewers no pueden "start document generation"); listarlo, consultarlo y descargarlo, de cualquier participante. Reintentar uno `failed` es de editor. — **Afecta a:** US-02, US-09, US-10.

> **Supuesto:** Pedir de nuevo un documento devuelve el mismo si hay uno en curso para la misma versión del viaje (`202`), o si hay uno `complete` cuyo contenido es idéntico, sin contar la hora de la copia (`200`). Si cambiaron el viaje o el estado de alguna reserva, se crea uno nuevo. — **Afecta a:** US-09.

> **Supuesto:** Si al descargar el archivo falta o no coincide con su hash, la propia descarga, aunque la haga un viewer, devuelve el documento a `waiting` y lo reencola para regenerarlo desde la copia. Es mantenimiento del sistema, no un cambio del usuario. Como el render es determinista, sale el mismo hash. — **Afecta a:** US-10.

> **Supuesto:** Tras `DOCUMENT_MAX_ATTEMPTS` fallos de generación el documento queda `failed` con el error, y no se reintenta solo. — **Afecta a:** US-09.

> **Supuesto:** El canal de estado es SSE por viaje (`GET /trips/{id}/events`) y anuncia cambios de versión del viaje, reservas y documentos; no repite eventos tras reconectar. Los clientes deben releer con `GET` al recibir `ready` o cualquier aviso. — **Afecta a:** US-02, US-08, US-09.

## Asistente y MCP (US-11, US-12)

## Datos de ejemplo y límites (destinos, reglas simplificadas)

> **Supuesto:** Las fixtures cubren Lisbon (10-14 de junio de 2030), Madrid (14-18) y Paris (18-22). London solo aparece como origen o destino de transporte. Fuera de esas fechas las búsquedas devuelven resultado vacío. — **Motivo:** el PRD permite limitar destinos y datos si el comportamiento sigue siendo demostrable. El detalle está en `mock/README.md`.

> **Supuesto:** El mock usa un solo worker; la garantía de "exactamente una reserva" ante peticiones concurrentes vale dentro de ese proceso. — **Motivo:** es un mock (ver ADR 0002).

## Preguntas sin resolver
