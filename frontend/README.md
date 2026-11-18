# frontend — UI mínima (React + Vite + TypeScript, sin librería de UI)

Permite completar los flujos del PRD y entender su resultado. El aspecto visual no es el objetivo. Las decisiones están en [ADR 0007](../docs/adr/0007-frontend-mismo-origen-y-estado-en-vivo.md).

## Arrancar

```bash
docker compose up --build      # desde la raíz: UI en http://localhost:5173
```

En desarrollo, con la API en marcha en `:8000`:

```bash
cd frontend && npm ci && npm run dev    # Vite hace de proxy de /api, igual que nginx en Compose
```

El selector de la cabecera elige el usuario de prueba (`ana`, `beto`, `carla`, `dani`), que se envía como `X-User-Id`.

## Qué hay en cada pantalla

- **Viajes:** la lista de los viajes en los que participas, con tu rol, y la creación de un viaje.
- **Viaje**, con la versión, tu rol y el estado del canal en vivo en la cabecera. Pestañas:
  - **Participantes:** la gestión solo se muestra al organizador. También permite editar los datos del viaje.
  - **Búsqueda:** cada resultado se ve distinto:
    - completo (verde);
    - parcial (ámbar), con los proveedores que no respondieron;
    - vacío (neutro);
    - fallido (rojo), con el código y `Retry-After`, sin presentarse nunca como "no hay opciones".
    - Muestra también la frescura (hace cuánto se recibió), el precio como estimado, la validez, la disponibilidad y las restricciones.
  - **Itinerario:** avisos, conflictos de horario, precio caducado, información que falta, actualizar cotización, reordenar y quitar.
  - **Costes:** estimado frente a confirmado, total, presupuesto restante y aviso si se supera.
  - **Reservas:**
    - revisar los términos exactos y confirmarlos (se envían `expectedRevision` y `expectedPrice`);
    - un `409 TERMS_CHANGED` muestra los términos actuales y ofrece actualizar la cotización;
    - cada reserva muestra su estado, `nextStep`, `lastError` y el fallo, y permite reintentar los borradores.
  - **Documentos:** generar, ver el estado y descargar. Avisa si el documento es de una versión anterior.
  - **Asistente:** chat con las tools usadas (argumentos, resultado y error) y el motivo de parada.

**Reglas de la UI**

- **Botones según el rol, pero el backend decide.** Con "Mostrar acciones aunque mi rol no las permita" se ven todas las acciones, y los `403` del backend se muestran con estado, código y `requestId` tal cual; el mensaje de los códigos conocidos aparece en español, con el original en un desplegable (ADR 0008).
- **409 de versión:** el panel muestra los cambios desde tu versión (quién, cuándo y qué), la vista pasa al estado actual y puedes **reaplicar** el mismo cambio sobre la versión actual o descartarlo.
- **Estado en vivo:** el SSE se lee con `fetch`, porque `EventSource` no envía cabeceras. Los eventos solo avisan: en cada (re)conexión llega `ready` y la UI relee todo con `GET`.
- **Nada parece confirmado si no lo está.** Solo una reserva `confirmed` aparece como reservada (en verde). `pending`, `submitted` y `unknown` dicen que aún no lo está; `unknown` además pide no reservar otra vez. Los costes distinguen estimado de confirmado.

## Pruebas

```bash
npm test                 # Vitest: lector SSE, cliente HTTP, presentación de estados, componentes
npm run typecheck
../scripts/e2e.sh        # Playwright contra Docker Compose levantado desde cero (ver abajo)
```

**Qué hace `scripts/e2e.sh`**

- Levanta un proyecto de Compose aparte (`tp-e2e`, con volúmenes propios y puertos 15173, 18000, 14010 y 55432), sin tocar el stack local ni sus datos.
- Deja `GEMINI_API_KEY` vacía, así que no hay llamadas al modelo.
- Ejecuta Playwright a través de nginx y elimina el proyecto al terminar. Con `KEEP=1` lo deja en marcha.

**Qué cubre el e2e**

- **Flujo completo:** crear, compartir, buscar, añadir, costes, reserva confirmada, documento descargado y asistente sin clave (503).
- **Permisos:** `403` al forzar acciones como lector y `404` para un no participante.
- **Concurrencia:** `409` con los cambios y la reaplicación, con el canal en vivo cortado, y relectura al reconectar.
- **Búsqueda:** resultado parcial, fallido y vacío.
- **Reserva `pending`:** no parece confirmada hasta que el proveedor la confirma, y el cambio llega en vivo.

Para comprobar que los tests detectan errores se inyectaron 11 mutaciones en la UI, y todas hacen fallar algún test:
- el e2e detecta las del flujo (releer al reconectar, reaplicar con la versión actual, eventos de reserva, términos enviados);
- Vitest detecta las de presentación (por ejemplo, que `unknown` parezca confirmada).
