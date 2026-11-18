# Guion de demo (10–15 minutos)

Se usan dos ventanas: el navegador en http://localhost:5173 y una terminal en la raíz del repo. Los clics se refieren a botones y etiquetas visibles de la UI.

## 0. Preparación (antes de empezar, ~2 min)

```bash
cp .env.example .env              # para el paso 7, poner GEMINI_API_KEY=... en .env
docker compose down -v            # opcional: empezar desde cero (BORRA los datos locales del stack)
docker compose up --build -d --wait
export B=http://localhost:4010    # mock del proveedor (/v1 es el contrato; /_control, solo demo)
curl -s -X DELETE $B/_control/scenario   # sin escenarios de fallo activos
```

Abrir http://localhost:5173. Arriba a la derecha está el selector **Usuario de prueba**.

## 1. Flujo completo (~4 min)

1. **Usuario de prueba:** `ana`. Clic en **Nuevo viaje**. Rellenar:
   - Nombre `Lisboa demo`;
   - Destinos `Lisbon`;
   - Desde `10/06/2030`, Hasta `14/06/2030`;
   - Viajeros `2`;
   - Presupuesto `3000.00`.

   Clic en **Crear**. La cabecera muestra el rol, `versión 1` y `estado: en vivo` (el canal SSE).
   - *Para enseñar la validación:* repetir con el presupuesto vacío; aparece `422 VALIDATION_ERROR` con el campo.
2. **Participantes:**
   - en **Añadir participante** elegir Beto, Rol `editor`, y clic en **Añadir**;
   - repetir con Carla, Rol `viewer`.
3. **Búsqueda:** Categoría `Alojamiento`, Destino `Lisbon`, clic en **Buscar**. Señalar:
   - el aviso verde "Búsqueda completa";
   - "recibido hace N s" (la frescura);
   - "precio estimado" y "válida hasta";
   - las restricciones;
   - *Three-night Belém boutique hotel* marcado **no disponible**.
4. Clic en **Añadir al itinerario** en *Four-night central hotel stay* (670.00 USD).
5. *(Opcional, conflicto de horario)* Búsqueda: Categoría `Actividad`, Desde y Hasta `11/06/2030`, **Buscar**. Añadir *Historic tram tour* y *Sintra day trip*, que se solapan el 11 de junio. En **Itinerario** aparece **Conflicto: Overlaps with 'Sintra day trip'.** (y viceversa) en los dos.
6. **Itinerario:** precio "estimado", "sin reservar", flechas ↑ ↓ para reordenar, **Actualizar cotización** y **Falta información** si la hay.
7. **Costes:** Estimado (sin confirmar) 670.00 USD, Confirmado 0.00 USD, presupuesto restante.
8. **Reservas:**
   - clic en **Revisar términos** en el hotel: precio, cotización, validez y restricciones;
   - clic en **Confirmar reserva por 670.00 USD**;
   - la tarjeta muestra **"Reservado: confirmado por el proveedor"**, el precio aceptado y la referencia del proveedor.
9. **Costes:** Confirmado por el proveedor 670.00 USD.
10. **Documentos:**
    - clic en **Generar documento de la versión N**;
    - el estado pasa de "En cola" o "Generándose" a **Listo** sin recargar (SSE);
    - clic en **Descargar** y abrir el HTML: itinerario, costes, estado de las reservas, versión y hora de la copia.

## 2. Permisos (~1,5 min)

1. Cambiar **Usuario de prueba** a `carla` y abrir *Lisboa demo*. Aparece `tu rol: viewer` y no hay botones de edición.
2. **Búsqueda** de alojamiento en Lisbon: no aparece **Añadir al itinerario**.
3. Marcar **"Mostrar acciones aunque mi rol no las permita"** y pulsar **Añadir al itinerario**. Aparece `403 FORBIDDEN` con "This action requires the editor role…", tal como lo devuelve el backend.
4. Cambiar a `dani` y recargar la URL del viaje (`#/trips/<id>`). Aparece `404 TRIP_NOT_FOUND`, la misma respuesta que para un viaje que no existe.
5. *(Opcional)* Las tools MCP aplican el mismo permiso. Se ve en `evals/deterministic/test_mcp_contract.py` (`uv run pytest evals/deterministic -k not_found`).

## 3. Conflicto 409 con reaplicación (~2 min)

El canal en vivo actualiza la versión en menos de un segundo, así que para provocar el conflicto a mano hay que cortarlo en la ventana de Beto.

1. Abrir una ventana privada (para que guarde su propio usuario) con `beto` en *Lisboa demo*.
2. En DevTools: **Network** → menú ⋮ → **More tools** → **Network request blocking**. Añadir el patrón `*events*` y recargar. La cabecera queda en "reconectando…".
3. En la ventana de `ana`: **Participantes** → **Editar datos del viaje**, cambiar el presupuesto a `2500.00` y **Guardar** (versión +1).
4. En la ventana de `beto`: **Búsqueda** de alojamiento en Lisbon y **Añadir al itinerario** en *Four-night Alfama apartment*. Aparece el panel **409 TRIP_VERSION_CONFLICT**:
   - "Tu cambio se basaba en la versión N; la actual es la N+1";
   - la lista de cambios (`vN+1 · ana · … · Updated budget`);
   - la cabecera ya muestra el presupuesto 2500.00.
5. Clic en **Reaplicar «añadir …» sobre la versión N+1**. Se guarda, la versión sube y el ítem aparece en **Itinerario**.
6. Desmarcar el bloqueo en DevTools. El estado vuelve a "en vivo" y la vista **se relee con GET** (`ready`): cualquier cambio hecho mientras tanto aparece sin depender de los eventos perdidos.

Alternativa sin DevTools: ver el `409` crudo con `curl` (enviando una versión vieja):

```bash
curl -s -X PATCH -H 'X-User-Id: ana' -H 'Content-Type: application/json' \
  http://localhost:8000/trips/<tripId> -d '{"version": 1, "name": "x"}' | python3 -m json.tool
```

## 4. Búsqueda parcial, fallida y vacía (~1,5 min)

```bash
curl -s -X POST $B/_control/scenario -H 'Content-Type: application/json' -d '{"supplierId":"supplier-b","mode":"unavailable"}'
```

1. **Buscar** alojamiento en Lisbon. Aparece el aviso ámbar **"Resultados parciales"**, con "Sin respuesta de supplier-b", y las ofertas de supplier-a siguen visibles y utilizables.

```bash
curl -s -X POST $B/_control/scenario -H 'Content-Type: application/json' -d '{"supplierId":"supplier-a","mode":"unavailable"}'
```

2. **Buscar** de nuevo. Aparece el aviso rojo **"La búsqueda falló: no es lo mismo que no haber opciones"**, con el código, y sin ofertas.

```bash
curl -s -X DELETE $B/_control/scenario
```

3. Destino `Reykjavik` y **Buscar**. Aparece el aviso neutro **"Sin opciones"**.

Variante: `"mode":"rate_limited"` muestra `Retry-After`.

## 5. Reserva con respuesta perdida, reconciliada (~3 min)

La API muere mientras espera la respuesta del proveedor. El proveedor sí creó la reserva. La app no la presenta como reservada y el worker la reconcilia por referencia, sin duplicarla.

1. Como `ana`, en un viaje de Lisbon, **añadir** *Four-night Baixa hostel beds* (o cualquier oferta de **supplier-a** sin reservar). Ir a **Reservas** → **Revisar términos**. **No confirmar todavía.**
2. En la terminal, hacer que supplier-a tarde 4 s en cada operación. Es menos que el plazo de la app (5 s), así que la consulta previa de la oferta pasa:

   ```bash
   curl -s -X POST $B/_control/scenario -H 'Content-Type: application/json' \
     -d '{"supplierId":"supplier-a","mode":"timeout","delaySeconds":4}'
   ```
3. Clic en **Confirmar reserva** y, en cuanto se pulse, ejecutar:

   ```bash
   sleep 6 && docker compose kill api   # entre la consulta previa (4 s) y la respuesta del POST (8 s)
   ```

   La UI muestra un error de red o `502` del proxy.
4. Volver a arrancar la API y quitar el retraso:

   ```bash
   curl -s -X DELETE $B/_control/scenario
   docker compose start api
   ```
5. Recargar. En **Reservas**, la tarjeta dice **"Enviando al proveedor (aún no reservada)"**, `booked` es falso y no aparece en Confirmado de **Costes**.
6. Esperar unos 60 s (`BOOKING_SUBMITTED_STALE_SECONDS`) sin tocar nada. La tarjeta pasa sola a **"Reservado: confirmado por el proveedor"** (SSE).
7. Ver cómo se resolvió:

   ```bash
   docker compose logs worker | grep -E "interrupted|by-reference|sync_booking"
   # … 1 interrupted booking submission(s) scheduled for reconciliation
   # … GET …/v1/bookings/by-reference/tp-… 200
   # … sync_booking -> succeeded
   ```

   La **Referencia propia** de la tarjeta (`tp-…`) es la que se consultó. El proveedor tiene una sola reserva para ella:

   ```bash
   curl -s -H 'Authorization: Bearer travel-demo-token' $B/v1/bookings/by-reference/<tp-...>
   ```

## 6. Documento regenerado tras borrar el archivo (~1,5 min)

1. En **Documentos** debe haber uno **Listo** (si no, **Generar** y esperar).
2. Borrar el archivo por debajo de la aplicación:

   ```bash
   docker compose exec api find /data/documents -name '*.html'
   docker compose exec api rm /data/documents/<tripId>/<documentId>.html
   ```
3. Clic en **Descargar**. Aparece `409 DOCUMENT_FILE_UNAVAILABLE`: "The stored file is missing or does not match its recorded checksum, so it was not delivered. It is being regenerated…". No se entrega un archivo roto.
4. El estado pasa a "En cola" y luego a **Listo** sin recargar. **Descargar** funciona y el `sha256` es el mismo que antes, porque se regenera desde la copia inmutable, no desde el viaje actual.

## 7. Asistente (~2 min)

Necesita `GEMINI_API_KEY` en `.env` y `docker compose up -d api` para que la API la lea. Sin clave, el asistente responde `503 ASSISTANT_UNAVAILABLE` y el resto funciona.

1. Como `carla` (viewer), en **Asistente**: *"Which Lisbon accommodation fits our budget and dates?"* Señalar:
   - la respuesta cita precios;
   - en **Herramientas usadas**, las llamadas MCP (`get_trip`, `get_costs`, `search_offers`…) con argumentos y resultados, y de ahí salen los importes.
2. *"Book the cheapest hotel for us now."* No reserva: explica que es de solo lectura y cómo hacerlo en la app. No existe ninguna tool de escritura.
3. *(Opcional)* Con supplier-a y supplier-b `unavailable` (paso 4): *"Find accommodation in Lisbon."* El asistente dice que la búsqueda falló, no que no haya opciones.

## Qué se ha ensayado de este guion

**Incidente y repetición.**
- Un primer ensayo de los pasos 1.5, 5 y 6 (2026-11-18, ~00:00) se hizo con un stack construido **mientras el runner de mutaciones tenía código mutado** (`if False:` en `reservations.py`). Ese ensayo no vale.
- Se repitió después:
  - con el código restaurado (`git diff` de `backend/` y `frontend/src` vacío, sin archivos `.orig`);
  - con la suite completa en verde;
  - con `tp-demo` reconstruido desde cero (`docker compose build --no-cache`, volúmenes nuevos);
  - comprobando que los 59 `.py` del backend dentro de las imágenes `api` y `worker` son idénticos byte a byte al repo.
- Los resultados de abajo son los de esa repetición. Desde entonces, `CLAUDE.md` prohíbe construir imágenes o ejecutar tests mientras corren mutaciones.

| Paso | Cómo se verificó | Estado |
| --- | --- | --- |
| 1. Flujo completo (salvo el conflicto opcional 1.5) | `frontend/e2e/flow.spec.ts` "flujo completo", con los mismos clics, en Docker desde cero (`scripts/e2e.sh`, 5 de 5 el 2026-11-18) | Ejecutado (automatizado) |
| 1.5 Conflicto de horario | Backend en `tests/integration/test_us04_itinerary.py`. Repetido por API en el stack limpio (2026-11-18 00:19): ambos ítems reciben `SCHEDULE_CONFLICT` ("Overlaps with 'Sintra day trip'." y viceversa). El presupuesto vacío da `422` | Por API, ejecutado. En la UI **no ensayado** |
| 2. Permisos 403 y 404 | e2e "permisos" | Ejecutado (automatizado) |
| 3. 409 con reaplicación y relectura al reconectar | e2e "409 de versión", con el canal cortado por Playwright en lugar de DevTools | Ejecutado (automatizado). El bloqueo con DevTools **no se ensayó a mano** |
| 4. Parcial, fallida y vacía | e2e "búsqueda parcial y fallida" | Ejecutado (automatizado) |
| 5. Respuesta perdida | Repetido con `curl` en lugar del clic, en el stack limpio (2026-11-18 00:17–00:19). El `POST` de la reserva recibió `502` del proxy al matar la API 6 s después de enviarlo. Tras reiniciar, la reserva estaba `submitted` (`booked=false`, `nextStep` "Being sent to the provider…"). A los ~56 s el worker registró "1 interrupted booking submission(s) scheduled for reconciliation", consultó `GET /v1/bookings/by-reference/tp-…` (`200`) y la reserva pasó a `confirmed` con `booking-000001`, la única reserva de ese mock recién creado. Que el cambio llegue en vivo a la tarjeta está cubierto por el e2e de la reserva `pending` | Ejecutado por API. El clic en la UI **no ensayado** |
| 6. Documento regenerado | Repetido con `curl` en el stack limpio (2026-11-18 00:19):<br>• documento `complete` (v2, sha256 `129388c8e155…`);<br>• no participante: `404`;<br>• tras `rm` del archivo, la descarga da `409 DOCUMENT_FILE_UNAVAILABLE` (`FILE_MISSING`) y el estado pasa a `waiting`;<br>• en ~1 s vuelve a `complete` con el **mismo** sha256;<br>• la descarga da `200` y el archivo descargado tiene ese hash | Ejecutado por API. El clic en la UI **no ensayado** |
| 7. Asistente con clave | Las evaluaciones con modelo (mismo endpoint, preguntas equivalentes) dieron 5 de 5 el 2026-11-17, ejecutadas por el candidato. En el e2e solo se ve el `503` sin clave | Desde la UI con clave **no ejecutado** |
