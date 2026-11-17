# 0003. Estados locales de reserva y reconciliación

- **Estado:** Aceptado
- **Fecha:** 2026-11-17
- **Historias relacionadas:** US-07, US-08 (también US-05 y US-09, que muestran estos estados)

## Contexto

US-08 exige distinguir entre pendiente, confirmado, fallido y desconocido localmente; no crear duplicados al reintentar; reconciliar los resultados desconocidos usando el contrato; distinguir una petición fallida de un resultado sin resolver; y no convertir nunca la incertidumbre en éxito ni borrar reservas confirmadas tras una interrupción.

El contrato (`POST /bookings`, `GET /bookings/by-reference/{clientReference}`) aporta:

- `Idempotency-Key` por caller: un reintento idéntico devuelve la misma reserva (`200`), aunque el quote haya cambiado.
- `clientReference` única: una clave nueva con una referencia ya aceptada da `409 REFERENCE_CONFLICT` y nunca crea otra reserva.
- `400`, `401`, `429` y `503` **no crean** reserva. Un fallo de transporte o una respuesta ausente **no prueban** el rechazo.
- `GET /bookings/by-reference` con `404` significa que no hay registro aceptado visible *en ese momento*; una creación en vuelo aún puede completarse.
- El proveedor solo conoce `pending`, `confirmed` y `failed`. "Unknown" es un estado del cliente.

## Estados locales

| Estado | Significado | ¿Terminal? |
| --- | --- | --- |
| `draft` | Intención de reserva creada localmente con términos revisados por el usuario (oferta, revisión, precio). Todavía no se ha enviado o el último envío no fue aceptado y es seguro reintentar. | No |
| `submitted` | Se persistió la intención (con `clientReference` e `Idempotency-Key` fijas) **antes** de enviar el `POST`; la petición está en vuelo. | No |
| `unknown` | Se envió el `POST` pero no sabemos si el proveedor lo aceptó: timeout, conexión cortada, 5xx sin cuerpo de error del contrato, o un `submitted` que quedó colgado por un reinicio. | No |
| `pending` | El proveedor aceptó y su resultado es `pending`. | No |
| `confirmed` | El proveedor aceptó y confirmó. | **Sí** |
| `failed` | El proveedor **aceptó** la petición y después la reserva falló (`status=failed` con `failure`). | **Sí** |
| `rejected` | El proveedor **no aceptó** la petición: `409 OFFER_CHANGED` u `OFFER_UNAVAILABLE`, `404` de oferta o `400`. No existe reserva en el proveedor. | **Sí** |

`failed` y `rejected` se distinguen a propósito: `failed` es un resultado del proveedor sobre una reserva aceptada (con su `id` y su snapshot), mientras que `rejected` significa que nunca hubo reserva. Para el usuario implican acciones distintas: en el primer caso, revisar el fallo del proveedor; en el segundo, revisar los nuevos términos y crear un intento nuevo.

## Transiciones

```
draft ──submit──▶ submitted ──201/200 pending──▶ pending ──▶ confirmed | failed
                     │  ──201/200 confirmed/failed──▶ confirmed | failed
                     │  ──409 OFFER_* / 404 / 400──▶ rejected
                     │  ──429 / 503 con Error del contrato──▶ draft   (no aceptado; reintento seguro)
                     └─ timeout / red / 5xx sin Error / reinicio ──▶ unknown
unknown ──(solo reconciliación)──▶ pending | confirmed | failed | rejected
```

Reglas:

1. **`confirmed`, `failed` y `rejected` nunca cambian.** El dominio rechaza cualquier transición desde un estado terminal. Reintentar tras un `rejected` crea **un intento nuevo** (nueva `clientReference` y nueva `Idempotency-Key`) con los términos que el usuario revise y acepte.
2. **Se persiste antes de enviar.** `draft → submitted` se guarda en la base de datos antes del `POST`. Si el proceso muere en ese momento, el registro queda en `submitted`, y no se pierde la clave con la que se envió.
3. **Un `submitted` antiguo pasa a `unknown`.** Si un registro lleva en `submitted` más tiempo que el timeout HTTP del proveedor más un margen, se asume que la respuesta se perdió (interrupción) y pasa a `unknown`. Nunca se asume éxito ni rechazo.
4. **`unknown` solo sale por reconciliación**:
   1. `GET /bookings/by-reference/{clientReference}`:
      - `200` → se adopta el estado del proveedor (`pending`, `confirmed` o `failed`), con su `id` y su snapshot.
      - `404` → puede seguir en vuelo, así que se mantiene `unknown` y se vuelve a consultar más tarde.
      - `429`, `503` o error de red → se mantiene `unknown` (no se inventa un resultado) y se reintenta con backoff.
   2. Si siguen dando `404` pasado un **plazo configurable** (`BOOKING_UNKNOWN_GRACE_SECONDS`, contado desde `submitted_at`), se **reenvía el mismo `POST` con la misma `Idempotency-Key` y el mismo cuerpo**:
      - `201` o `200` → se adopta el estado devuelto. Si el original llegó a aceptarse, el proveedor lo devuelve en lugar de duplicarlo.
      - `409 OFFER_CHANGED` / `OFFER_UNAVAILABLE` → `rejected`. Si el original se hubiera aceptado, la idempotencia habría devuelto esa reserva; por tanto, no se aceptó.
      - `409 REFERENCE_CONFLICT` → existe una reserva aceptada con esa referencia, así que se vuelve al paso 1.
      - `429`, `503` o red → se mantiene `unknown` y se reintenta más tarde.
5. **`pending` se consulta** con `GET /bookings/{id}` hasta que llegue a `confirmed` o `failed`. Si la consulta falla, la reserva sigue en `pending` (el proveedor ya la aceptó); nunca se degrada a `unknown`.
6. **Los datos aceptados son inmutables.** Cuando se adopta una reserva del proveedor se guardan su `id`, la oferta aceptada y el precio, y no se vuelven a sobrescribir con datos posteriores del proveedor (US-05: los precios confirmados siguen siendo comprensibles aunque cambien los del proveedor).
7. **Cada transición queda registrada** con `updated_at` y `last_error` (código o mensaje y `requestId` del proveedor, si existe) para poder diagnosticarla.

## Opciones descartadas

- **Solo `pending`/`confirmed`/`failed` (espejo del proveedor).** Obliga a elegir un estado falso cuando no sabemos si se aceptó; viola US-08 ("no convertir la incertidumbre en éxito").
- **Tratar timeout como fallo y permitir reintento con clave nueva.** Puede crear reservas duplicadas si el original se aceptó.
- **Reintentar el `POST` inmediatamente en vez de consultar primero.** Es seguro gracias a la idempotencia, pero si los términos han cambiado se obtiene `OFFER_CHANGED`, y sin consultar antes no se puede distinguir de forma barata "no llegó" de "llegó y no lo vimos". Consultar primero responde la pregunta sin efectos secundarios; el reenvío solo se usa cuando el `404` persiste.
- **Fusionar `failed` y `rejected`.** Oculta si existe una reserva en el proveedor, que es justo lo que el usuario necesita saber.

## Consecuencias

- La máquina de estados vive en el dominio (`backend/app/domain/bookings.py`) y se prueba con tests unitarios. Las transiciones no permitidas lanzan `InvalidTransition`.
- La reconciliación es trabajo en segundo plano (worker), idempotente y reanudable: vuelve a leer el estado en cada intento.
- Nuevas configuraciones: `BOOKING_UNKNOWN_GRACE_SECONDS` y el margen de `submitted` colgado.
- Dependencia del contrato: concluir `rejected` a partir del reenvío se apoya en que el proveedor resuelve la `Idempotency-Key` antes de evaluar los términos, y en que las peticiones idénticas concurrentes devuelven el mismo registro ("Concurrent identical requests create exactly one booking; the others return that same record"). Si el proveedor incumpliera eso con una creación aún en vuelo, podríamos marcar `rejected` una reserva que acaba aceptándose. Lo mitigamos con un plazo de gracia generoso y, para la revisión, queda registrado como límite conocido.

## Verificación

- Unitarios: todas las transiciones permitidas y prohibidas; los estados terminales son inmutables; `unknown` solo sale hacia estados respaldados por el proveedor o hacia `rejected`.
- Integración (US-08) contra el mock: respuesta perdida (modo `timeout`), `404` y luego aparición, reenvío idempotente, `429` y `503` en la reconciliación, `pending` → `settle`.
