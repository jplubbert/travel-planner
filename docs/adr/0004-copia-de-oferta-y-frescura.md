# 0004. Copia de la oferta en el ítem y política de frescura

- **Estado:** Aceptado
- **Fecha:** 2026-11-17
- **Historias relacionadas:** US-03, US-04, US-05, US-07 (y US-10, que muestra los mismos datos)

## Contexto

- Los precios del proveedor son **cotizaciones** con `revision` y `validUntil`. Cambian, caducan y no reservan inventario (contrato `getOffer`, `searchOffers`).
- US-04 pide que el itinerario muestre horarios y costes y que marque los precios expirados y las opciones no disponibles. Los cambios guardados deben seguir ahí al reabrir el viaje.
- US-05 pide distinguir estimaciones de precios confirmados, y que los confirmados se sigan entendiendo aunque el proveedor cambie sus precios.
- US-03 pide que la presentación deje clara la frescura. La guía valora "reutilizar resultados sin presentar datos obsoletos como confirmados" y justificar el equilibrio con el rendimiento.
- El cliente (navegador o asistente) no es una fuente fiable de precios.

Hay que decidir qué guarda un ítem del itinerario sobre su oferta y cuándo se consulta al proveedor.

## Opciones evaluadas

### A. El ítem guarda solo `offerId` y lee la oferta en vivo en cada lectura

- ✅ Siempre lo último.
- ❌ Cada lectura del itinerario y de los costes hace N llamadas al proveedor: lento, sensible a 429 y 503, y un proveedor caído deja el itinerario sin horarios ni costes.
- ❌ El coste "estimado" cambia sin que nadie lo haya decidido, lo que hace los totales irreproducibles y pierde el historial que verá US-10.

### B. Caché de búsquedas compartida (TTL) de la que leen el itinerario y los costes

- ✅ Menos llamadas.
- ❌ Hay que invalidarla y elegir un TTL. Si no coincide con `validUntil`, presenta como vigente una cotización caducada, o al revés.
- ❌ Una búsqueda es de un grupo y una ventana de fechas concretos; reutilizarla para otra consulta es incorrecto.
- ❌ Es otro estado que mantener coherente entre réplicas. No hay un problema de rendimiento medido que lo justifique.

### C. El cliente envía la oferta (o su precio) al añadirla

- ✅ Ninguna llamada extra.
- ❌ El servidor no puede confiar en un precio enviado por el cliente: los costes y el `expectedPrice` de la reserva dependerían de él.

### D. Copia de la oferta en el ítem, tomada del proveedor, y actualización a demanda (elegida)

## Decisión

1. **Al añadir un ítem, el backend pide la oferta al proveedor** (`GET /offers/{offerId}`) y guarda una **copia completa** en el ítem:
   - columnas tipadas para la lógica (precio `numeric(12,2)`, `revision`, `available`, `validUntil`, horario, viajeros, restricciones);
   - `offer_snapshot`, el JSON exacto en formato del contrato, que conserva los offsets locales. `timestamptz` guarda el instante pero no el offset, y las fechas locales importan para comprobar si un ítem cae dentro del viaje;
   - `quoted_at`, el momento en que se obtuvo.

   El cuerpo de la petición solo acepta `offerId` (y `version`); un `price` enviado por el cliente da `422`.
2. **No se cachean búsquedas.** Cada búsqueda consulta al proveedor y devuelve `searchedAt`, `receivedAt` y `validUntil` por oferta (US-03).
3. **Las lecturas del itinerario y de los costes no llaman al proveedor.** Usan la copia y calculan en el momento de la lectura:
   - `quote.expired` / `PRICE_EXPIRED`, si `ahora ≥ validUntil`;
   - `OFFER_UNAVAILABLE`, según `available` en la última cotización.

   Todo importe sin reserva confirmada se marca como `estimate`.
4. **La actualización es explícita y a demanda:** `POST /trips/{id}/itinerary/items/{itemId}/refresh`. Sustituye la copia por la cotización actual del proveedor.
   - Es una mutación con versión (ADR 0001) que queda en el historial con un resumen de lo que cambió (precio, disponibilidad, horario, validez).
   - Si la cotización no cambió, no guarda nada ni sube la versión.
   - Si el proveedor falla, la copia anterior queda intacta.
5. **El precio confirmado es otra cosa.** El coste de un ítem con reserva `confirmed` usa el `accepted_price` y la oferta aceptada que guarda la reserva (ADR 0003), nunca la copia del ítem. Actualizar la copia después no cambia lo confirmado.
6. Antes de reservar (US-07), el backend vuelve a pedir la oferta, porque la copia del ítem puede estar obsoleta. La copia sirve para planificar, no para aceptar términos.

## Consecuencias

**Positivas**

- Leer el itinerario y los costes es rápido, determinista y funciona con el proveedor caído. Las estimaciones solo cambian cuando un editor decide actualizar, y ese cambio queda en el historial.
- La frescura es explícita en tres niveles: `quotedAt`, `validUntil` y `expired`/`PRICE_EXPIRED`. Nada obsoleto se presenta como vigente ni como confirmado.
- El servidor nunca depende de precios enviados por el cliente.

**Negativas / riesgos**

- La disponibilidad mostrada puede estar desfasada, y se indica explícitamente ("as of quotedAt"). Una opción puede haberse agotado sin que el itinerario lo sepa hasta que alguien la actualice o intente reservar.
- Actualizar es manual, ítem a ítem. Un "actualizar todo" o una actualización en segundo plano son mejoras posibles, que tendrían que respetar los límites de tasa del proveedor.
- La copia duplica datos del proveedor; es intencional (historia y reproducibilidad).

## Verificación

- `tests/integration/test_us04_itinerary.py`:
  - el precio viene del proveedor y un precio enviado por el cliente se rechaza;
  - la actualización registra el cambio, no guarda nada si la cotización no cambió y conserva la copia si el proveedor falla;
  - se marcan las cotizaciones expiradas y las ofertas no disponibles.
- `tests/integration/test_us05_costs.py`: el precio confirmado sobrevive a un cambio de precio del proveedor seguido de una actualización.
- `tests/integration/test_persistence.py`: el offset local sobrevive al ida y vuelta por la base de datos, y la migración 0003 rellena la copia de los ítems existentes.
