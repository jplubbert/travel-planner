# 0007. Frontend en el mismo origen, SSE leído con fetch y la UI que no es barrera de permisos

- **Estado:** Aceptado
- **Fecha:** 2026-11-17
- **Historias relacionadas:** US-01 … US-12 (flujos en la UI), US-02, US-06, US-08

## Contexto

- La guía pide un frontend que permita completar los flujos y entender sus resultados; el aspecto visual no cuenta. También pide contratos claros entre frontend y backend, errores comprensibles y recuperación tras desconexiones sin suponer que llegó cada notificación.
- La identidad de prueba viaja en la cabecera `X-User-Id`, y `EventSource` no permite enviar cabeceras.
- El backend ya es quien aplica los permisos (ADR 0001, US-02) y resuelve la concurrencia con un `409` que trae el estado actual y los cambios.

## Decisión 1: React + Vite + TypeScript, sin librería de UI ni de estado

**Opciones evaluadas**

- **HTML generado por el servidor (Jinja + htmx).** Menos piezas, pero mezcla presentación en el backend, cuyo foco es la API, y complica el estado en vivo por pestaña.
- **React con una librería de componentes o de estado** (MUI, Redux, React Query). Aportan poco a esta escala y añaden superficie que hay que poder explicar.
- **React + Vite + TypeScript, con `fetch` y estado local (elegida).** Los tipos del contrato están en `src/types.ts`, escritos a partir de los serializadores de la API.

## Decisión 2: un solo origen, con nginx como proxy de `/api`

**Opciones evaluadas**

- **CORS en la API.** Habría que configurar orígenes y cabeceras permitidas (`X-User-Id`) en cada entorno.
- **nginx sirve la UI y hace de proxy de `/api` hacia la API (elegida).** En desarrollo hace lo mismo el proxy de Vite. La ruta `/trips/{id}/events` va sin buffer y con lectura larga, para que los eventos lleguen al momento.

## Decisión 3: SSE leído con `fetch`, y los eventos son solo avisos

- `src/sse.ts` lee el stream con `fetch` (que sí envía `X-User-Id`) y un parser incremental. Reconecta respetando `retry`, y se detiene ante `access-revoked`.
- En cada (re)conexión el backend envía `ready` y la UI **relee con `GET`** el viaje y las pestañas abiertas. Un evento perdido o duplicado no deja datos viejos: el estado mostrado siempre viene de un `GET`.

## Decisión 4: la UI no es la barrera de permisos

- Las acciones se muestran según `myRole`, solo para orientar al usuario.
- Los `403`, `404` y `409` del backend se muestran **tal cual**: estado, código, mensaje, detalles y `requestId`.
- La opción "Mostrar acciones aunque mi rol no las permita" deja comprobar en la demo que el backend las rechaza.

## Decisión 5: un 409 de versión se resuelve en un panel con reaplicación

- Todas las mutaciones con versión pasan por `versioned(label, op)` (`src/TripView.tsx`).
- Ante un `409 TRIP_VERSION_CONFLICT`:
  - no se aplica nada;
  - la vista pasa al estado actual que trae el `409`;
  - el panel lista `changesSinceYourVersion` (versión, quién, cuándo y qué).
- El usuario puede **reaplicar** la misma operación sobre `currentVersion` o descartarla. Si mientras tanto hubo otro cambio, aparece el nuevo conflicto.
- No hay fusión automática: el usuario ve qué cambió antes de decidir.

## Consecuencias

- **Positivas:**
  - la UI no duplica reglas de negocio;
  - lo que muestra el estado viene siempre de un `GET`;
  - un solo origen, sin configuración de CORS.
- **Negativas:**
  - estado local por pestaña (sin caché compartida), así que se repiten algunas lecturas; a esta escala no importa;
  - la identidad sigue siendo la cabecera de prueba, como en la API.

## Verificación

- `frontend/src/*.test.ts(x)` (Vitest):
  - parser y reconexión del SSE;
  - errores mostrados sin reescribir;
  - solo `confirmed` parece reservada;
  - los cuatro resultados de búsqueda se ven distintos;
  - panel del 409.
- `frontend/e2e/flow.spec.ts` (Playwright, con `scripts/e2e.sh` contra Compose desde cero):
  - flujo completo;
  - `403` y `404`;
  - `409` con reaplicación y relectura al reconectar;
  - búsqueda parcial, fallida y vacía;
  - reserva `pending` actualizada en vivo.
