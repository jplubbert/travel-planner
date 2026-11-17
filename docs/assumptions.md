# Supuestos y preguntas abiertas

Registro de ambigüedades del PRD (`assignment/prd.md`), el candidate guide y el contrato del proveedor (`assignment/openAPI.json`), y cómo se resolvieron. Formato de cada entrada:

> **Supuesto:** … — **Afecta a:** US-XX / sección — **Motivo:** … — **Impacto si es incorrecto:** …

## Generales

## Identidad y permisos (US-02, US-12)

## Viajes e itinerario (US-01, US-04, US-06)

## Búsqueda y proveedor (US-03, contrato OpenAPI)

> **Supuesto:** "Las opciones encajan con el grupo" significa que cada oferta se cotiza para el tamaño exacto del grupo pedido. El mock materializa cada fixture por número de viajeros (`offer-<fixture>-p<n>`) y excluye las que superan su capacidad. — **Afecta a:** contrato `searchOffers`, US-03, US-05 — **Motivo:** el contrato dice que el precio cubre a todo el grupo y que las opciones deben encajar en él. — **Impacto si es incorrecto:** bajo; solo cambia cómo se generan los IDs en el mock.

> **Supuesto:** Una oferta encaja en la ventana si su fecha local de inicio y su fecha local de fin están dentro de `[startsOn, endsOn]`. En transporte, la salida usa el offset del origen y la llegada el del destino. — **Afecta a:** `searchOffers` — **Motivo:** el contrato habla de una ventana "destination-local" pero no precisa qué extremos deben caer dentro. — **Impacto si es incorrecto:** un vuelo nocturno que llega al día siguiente del final de la ventana quedaría excluido.

> **Supuesto:** Un `providerId` que existe pero no ofrece la categoría pedida devuelve `400`, igual que uno desconocido. Un destino sin fixtures devuelve `200` vacío y completo. — **Afecta a:** `searchOffers` — **Motivo:** el contrato solo define el caso de supplier desconocido. — **Impacto si es incorrecto:** la app debe tratar ambos como error de petición, no como fallo del proveedor.

> **Supuesto:** Si todos los suppliers consultados fallan, se responde `503` aunque todos estuvieran limitados por tasa. Solo una consulta a un único supplier limitado da `429`. — **Afecta a:** `searchOffers` — **Motivo:** es la lectura literal de "return 503 or 429 for a rate-limited single-supplier query".

> **Supuesto:** Un escenario de supplier también afecta a los replays y a las consultas de reservas de ese supplier, no solo a la búsqueda y a las reservas nuevas. — **Afecta a:** reconciliación (US-08) — **Motivo:** así se puede demostrar que una reconciliación que falla deja el estado como desconocido en lugar de inventarlo.

## Costes y redondeo (US-05)

> **Supuesto:** Los precios del mock se calculan como `base + por_viajero × viajeros` con `Decimal`, redondeando *half-up* a centavos. La app nunca recalcula un precio del proveedor; solo suma importes ya redondeados. — **Afecta a:** US-05.

## Reservas (US-07, US-08)

> **Supuesto:** Si una oferta no está disponible, el proveedor responde `OFFER_UNAVAILABLE` antes de comparar revisión y precio, aunque la revisión también haya cambiado. — **Afecta a:** `createBooking`, US-07 — **Motivo:** el contrato no fija la precedencia, y "no disponible" es la información más útil para el usuario.

> **Supuesto:** Una petición de reserva rechazada (4xx/5xx) no registra la `Idempotency-Key` ni la `clientReference`; solo las reservas aceptadas lo hacen. — **Afecta a:** `createBooking`, US-07/US-08 — **Motivo:** el contrato dice que el rechazo "does not create a booking" y que las claves se resuelven contra requests aceptados. — **Impacto si es incorrecto:** tras un `OFFER_CHANGED` la app podría reutilizar la clave con nuevos términos; aun así, la app usará una clave nueva por cada intento con términos distintos.

> **Supuesto:** `clientReference` es única por caller durante toda la vida del dataset del mock. — **Afecta a:** `createBooking` — **Motivo:** el schema la define como "unique within the caller's dataset".

## Documentos (US-09, US-10)

## Asistente y MCP (US-11, US-12)

## Datos de ejemplo y límites (destinos, reglas simplificadas)

> **Supuesto:** Las fixtures cubren Lisbon (10-14 de junio de 2030), Madrid (14-18) y Paris (18-22). London solo aparece como origen o destino de transporte. Fuera de esas fechas las búsquedas devuelven resultado vacío. — **Motivo:** el PRD permite limitar destinos y datos si el comportamiento sigue siendo demostrable. El detalle está en `mock/README.md`.

> **Supuesto:** El mock usa un solo worker; la garantía de "exactamente una reserva" ante peticiones concurrentes vale dentro de ese proceso. — **Motivo:** es un mock (ver ADR 0002).

## Preguntas sin resolver
