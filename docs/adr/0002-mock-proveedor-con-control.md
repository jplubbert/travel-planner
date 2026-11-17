# 0002. Mock del proveedor con fixtures base, controles fuera del contrato y reloj controlable

- **Estado:** Aceptado
- **Fecha:** 2026-11-17
- **Historias relacionadas:** US-03, US-07, US-08 (y toda la evidencia de fallos)

## Contexto

- `assignment/openAPI.json` define el proveedor externo: búsqueda multi-supplier con resultados parciales, quotes con `revision` y `validUntil`, reservas idempotentes por caller, `REFERENCE_CONFLICT`, `OFFER_CHANGED`, `OFFER_UNAVAILABLE`, 429/503 y reconciliación por `clientReference`. Exige que reservas, referencias e idempotencia sobrevivan a reinicios normales del proveedor, y que las fixtures usen fechas futuras **o un reloj controlable documentado** para que validez y expiración sean reproducibles.
- La guía pide **escenarios de fallo reproducibles** ("reproducible failure scenarios", "checks that can detect real failures rather than only verify prepared mock responses") y una demo de fallos y concurrencia.
- La guía también dice que la aplicación debe consumir el proveedor **por su contrato público, no por su base de datos ni por sus controles de test**, y que los endpoints de control quedan fuera del contrato.

Necesitamos, por tanto, un mock que sea fiel al contrato, determinista para tests y demos, y capaz de inyectar fallos, sin que la aplicación pueda acoplarse a esa capacidad.

## Opciones evaluadas

### A. Fallos aleatorios (probabilidad configurable de 503/timeout/429)

- ✅ Trivial de implementar; se parece al mundo real.
- ❌ No reproducible: un test o una demo puede pasar o fallar según la suerte.
- ❌ Imposible fijar "supplier-b caído mientras supplier-a responde" para demostrar resultados parciales a voluntad.

### B. Solo fixtures fijas (datos y comportamientos codificados, sin control en tiempo de ejecución)

- ✅ Determinista y simple; sin superficie extra.
- ❌ Para cada escenario hace falta una fixture "trampa" (oferta que siempre falla, supplier que siempre está caído), lo que contamina los datos normales.
- ❌ No permite expirar un quote o cambiar un precio en mitad de un flujo (US-07) sin esperar en tiempo real.

### C. Endpoints de control (fuera del contrato) sobre fixtures base

- ✅ Determinista y a voluntad: el test o el presentador activa exactamente el fallo que quiere demostrar y lo desactiva después.
- ✅ Los datos base siguen siendo "normales".
- ❌ Superficie adicional que la aplicación podría usar indebidamente si no se separa con claridad.

## Decisión

**Fixtures base + endpoints `/_control/*` fuera del contrato + reloj controlable.**

- **Fixtures base deterministas:** destinos Lisbon, Madrid y Paris (junio de 2030), tres suppliers lógicos (`supplier-a`, `supplier-b`, `supplier-c`), al menos 4 ofertas por categoría y destino. Algunas fixtures tienen comportamiento propio (oferta no disponible, reserva que queda `pending`, reserva que termina `failed`) para cubrir los casos sin activar nada.
- **Contrato bajo `/v1`, controles bajo `/_control`:** distintas rutas, sin token del proveedor, documentadas solo en `mock/README.md`. La aplicación solo conoce `PROVIDER_BASE_URL` (que apunta a `/v1`); ningún código de la aplicación ni del asistente puede llamar a `/_control` (se verificará con un test de arquitectura en el backend).
- **Controles:** `/_control/scenario` (por supplier: `unavailable`, `timeout`, `rate_limited`, y resultado forzado de reservas), `/_control/clock` (fijar/avanzar la hora), `/_control/reset`, `/_control/booking/{id}/settle` (pending → confirmed/failed) y `/_control/offers/{id}` (cambiar precio/disponibilidad para provocar `OFFER_CHANGED`/`OFFER_UNAVAILABLE`).
- **Reloj controlable:** toda la lógica temporal del mock (`validUntil`, `searchedAt`, `createdAt`, `updatedAt`) usa un reloj inyectado. Por defecto es la hora del sistema; los tests y la demo lo congelan o avanzan.
- **Persistencia:** el estado de datos (reservas, idempotencia, referencias, quotes) se guarda en un archivo JSON con escritura atómica **antes** de responder. El estado de control (escenarios, reloj) es efímero: un reinicio lo limpia.

## Consecuencias

**Positivas**

- Cada escenario de fallo de la demo y de los tests es reproducible con una llamada explícita.
- La expiración de quotes se prueba en milisegundos avanzando el reloj.
- El modo `timeout` aplicado a reservas **crea la reserva y retrasa la respuesta**, que es exactamente el caso "respuesta perdida" de US-08.

**Negativas / riesgos**

- El mock es un único proceso con lock en memoria: la garantía de "exactamente una reserva ante peticiones concurrentes idénticas" vale para una réplica. Es suficiente para un mock; se documenta.
- Persistir en un JSON completo en cada escritura no escala; irrelevante para el volumen de un mock.
- Los controles son una puerta trasera: se mitiga con rutas separadas, documentación y un test que impida su uso desde la aplicación.

**Verificación**

- Tests del mock (`mock/tests/`) que validan cada respuesta contra los schemas de `assignment/openAPI.json` con `jsonschema` (Draft 2020-12, con comprobación de formatos), incluyendo idempotencia concurrente, persistencia tras reinicio y respuesta perdida.
