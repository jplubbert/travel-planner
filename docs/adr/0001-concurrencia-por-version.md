# 0001. Concurrencia en edición de viajes por versión optimista

- **Estado:** Aceptado
- **Fecha:** 2026-11-17
- **Historias relacionadas:** US-06 (principal), US-02, US-04

## Contexto

US-06 — *Edit without silently losing changes* exige:

- Las ediciones concurrentes no descartan silenciosamente cambios ya guardados.
- Cada participante puede saber si su cambio se guardó.
- Quien sufre un conflicto entiende qué pasó y puede resolverlo.
- El manejo del conflicto preserva permisos y la consistencia del viaje.
- Presencia en vivo y edición colaborativa en tiempo real son **opcionales**; evitar pérdidas es **obligatorio**.

Varios editores pueden modificar el mismo viaje (datos del viaje e itinerario) desde sesiones distintas, con pestañas abiertas durante minutos. El cliente trabaja sobre una copia que puede quedar obsoleta. Las reservas (US-08) y documentos (US-09) tienen sus propios mecanismos de idempotencia y estado y no forman parte de esta decisión.

## Opciones evaluadas

### A. Bloqueo pesimista (lock de edición)

El editor "toma" el viaje (lock en BD o fila `locked_by` con TTL) y nadie más puede editar hasta que lo libere o expire.

- ✅ Imposible tener conflictos de escritura mientras el lock está vigente.
- ❌ Un lock mantenido entre peticiones HTTP necesita TTL, renovación y liberación: si el usuario cierra la pestaña, los demás quedan bloqueados hasta que expire.
- ❌ Mala experiencia para grupos: bloquea incluso ediciones que no se pisan.
- ❌ Un `SELECT … FOR UPDATE` solo sirve dentro de una transacción; no protege la ventana "leí → pensé → guardé" del usuario, que es donde ocurre la pérdida.

### B. Fusión automática (last-write-wins por campo, CRDT/OT)

El servidor combina los cambios concurrentes automáticamente.

- ✅ Sin interrupciones para el usuario en el caso común.
- ❌ Last-write-wins **es** pérdida silenciosa, justo lo que prohíbe US-06.
- ❌ CRDT/OT es mucho más complejo, pensado para colaboración en tiempo real (opcional en el PRD), y aún puede producir itinerarios semánticamente inconsistentes (dos actividades solapadas, presupuesto excedido) sin que nadie lo decida.
- ❌ Coste desproporcionado para el plazo.

### C. Control optimista por versión con 409

Cada viaje tiene un entero `version`. Toda lectura lo devuelve; toda escritura debe enviar la versión sobre la que se basó. El servidor aplica la escritura solo si coincide y la incrementa; si no, responde `409 Conflict`.

- ✅ Ningún cambio guardado se sobrescribe sin que el autor haya visto la versión vigente.
- ✅ Sin estado de sesión ni locks que expirar; funciona con desconexiones y reintentos.
- ✅ Simple de implementar de forma atómica (`UPDATE … SET version = version + 1 WHERE id = :id AND version = :expected`) y de probar con peticiones concurrentes reales.
- ✅ El resultado es explícito: éxito con nueva versión, o 409 con el estado actual.
- ❌ El usuario que pierde la carrera tiene que reaplicar su cambio.
- ❌ Con una versión por viaje hay "falsos conflictos" entre ediciones que no se pisan (p. ej. dos ítems distintos).

## Decisión

**Opción C: control optimista con versión a nivel de agregado `Trip`.**

- `Trip` (metadatos + itinerario) es un agregado con un único `version: int`.
- Las respuestas de lectura incluyen `version` (cuerpo y cabecera `ETag`).
- Toda mutación exige la versión esperada (campo `version` en el cuerpo del `PATCH`; las lecturas también la exponen en `ETag`. Corrección menor del 2026-11-17 al implementarlo). Falta de versión → `400` con código `VERSION_REQUIRED` y un mensaje que explique que hay que enviar la versión leída; no se aplica. (Se descartó `428 Precondition Required`: es semánticamente correcto pero poco conocido, y el objetivo es que los errores sean entendibles.)
- La comprobación y el incremento ocurren en la **misma sentencia/transacción** que la escritura, en el adaptador de persistencia; el dominio expresa la regla mediante el puerto de repositorio (`save(trip, expected_version)` → éxito o `VersionConflict`).
- En conflicto: `409` con código `TRIP_VERSION_CONFLICT`, la versión actual y el estado actual del viaje (y, cuando sea posible, quién/cuándo lo cambió), para que el cliente muestre la diferencia y el usuario reaplique o descarte.
- La autorización se evalúa **antes** de comprobar la versión y de nuevo en el reintento: un conflicto nunca revela datos a quien no tiene acceso ni permite a un viewer escribir.
- Las validaciones de consistencia del itinerario se ejecutan sobre el estado resultante en cada escritura aceptada.

Se acepta la granularidad gruesa (versión por viaje) por simplicidad; si los falsos conflictos resultan molestos, se puede evolucionar a versión por ítem en un ADR posterior sin cambiar el contrato básico (409 + estado actual).

## Consecuencias

**Positivas**

- Cumple US-06: no hay pérdida silenciosa; el usuario sabe si guardó (2xx con nueva versión) o no (409).
- Sin locks huérfanos ni dependencia de conexiones persistentes.
- Compatible con la recuperación tras desconexión: el cliente siempre puede releer el estado y la versión actual.

**Negativas / riesgos**

- Más fricción cuando dos editores trabajan a la vez sobre el mismo viaje.
- El frontend debe manejar el 409 (mostrar estado actual, permitir reaplicar).
- Todas las rutas de escritura deben pasar por el mismo mecanismo; una ruta que lo salte rompería la garantía.

**Verificación (tests requeridos)**

- Dos actualizaciones concurrentes con la misma versión base: exactamente una tiene éxito, la otra recibe 409, y el cambio guardado sigue presente.
- Escritura sin versión → 400 `VERSION_REQUIRED` y sin cambios.
- Un viewer o no participante recibe 403/404 antes que cualquier 409, y el 409 no filtra datos.
- La versión se incrementa exactamente una vez por escritura aceptada.
