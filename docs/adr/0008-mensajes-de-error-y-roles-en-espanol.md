# 0008. Roles y mensajes de error conocidos en español en la UI

- **Estado:** Aceptado. Sustituye en parte la decisión 4 de ADR 0007: el *mensaje* de un error conocido ya no se muestra tal cual.
- **Fecha:** 2026-11-18
- **Historias relacionadas:** US-02 (roles y 403), US-01 y US-06 (validación, `VERSION_REQUIRED`, `409` de versión)

## Contexto

- La UI está en español, pero mostraba los roles con los valores de la API (`viewer`) y los mensajes de error en inglés tal como los devuelve el backend. Un mismo panel mezclaba los dos idiomas.
- El contrato del backend no cambia: los roles siguen siendo `organizer`, `editor` y `viewer`, y los errores llevan `code`, `message`, `details` y `requestId`. El `code` es estable; el `message` es texto libre.
- ADR 0007 (decisión 4) pide que los `403`/`409` se muestren tal cual para que se vea qué rechazó el backend y por qué.

## Opciones evaluadas

1. **Traducir en el backend.** Cambia el contrato y los textos que ya usan tests, evaluaciones y el asistente. Descartada: el encargo es no tocar el backend.
2. **Traducir en el frontend por `code`, conservando el original.** El estado, el código, los detalles y el `requestId` siguen tal cual; solo cambia el texto principal, y el mensaje original del backend sigue disponible en un desplegable.
3. **Dejarlo como está.** Mantiene la mezcla de idiomas.

## Decisión

Opción 2.

- `ROLE_LABEL` (`src/format.ts`) traduce los roles: organizador, editor, lector. Badges, tabla y selects de participantes usan la etiqueta; los selects siguen enviando el valor de la API.
- `errorMessage(code, message)` traduce `FORBIDDEN`, `TRIP_VERSION_CONFLICT`, `VERSION_REQUIRED` y `VALIDATION_ERROR`. Para `FORBIDDEN` extrae los roles del mensaje ("requires the X role; you are a Y") y, si el texto tiene otra forma, usa uno genérico. Cualquier otro código conserva el mensaje del backend.
- `ErrorBox` muestra estado y código tal cual, el texto en español y, si difiere, "Mensaje original del backend" en un `<details>`.

## Consecuencias

- La UI habla un solo idioma en los casos frecuentes, y la decisión de fondo de ADR 0007 se mantiene: el backend decide y la UI muestra su respuesta, con el código y el original visibles.
- Los mensajes por campo de `VALIDATION_ERROR` (`details.fields[].message`) y los de códigos no listados siguen en inglés: son muchos y de texto libre.
- Leer los roles del texto del `403` depende de su redacción. Si cambia, la UI cae al texto genérico, sin errores.
- **Verificación:** `src/format.test.ts` (roles y traducción por código, con caída al genérico y a los códigos desconocidos), `src/components.test.tsx` (`ErrorBox` con estado, código, original y `requestId`), `src/tabs/ParticipantsTab.test.tsx` (etiquetas y valores de los selects) y `e2e/flow.spec.ts` (`tu rol: lector` y el `403` traducido contra Compose).
