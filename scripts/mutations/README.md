# Inyección de errores (mutaciones)

Comprueba que los tests detectan fallos reales y no solo confirman respuestas preparadas. Cada mutación quita una comprobación, cambia un estado o rompe una garantía. El runner la aplica, ejecuta los tests que deberían detectarla y restaura el archivo.

- **Detectada:** algún test falla.
- **Sobrevive:** todo pasa. O falta un test, o hay otra capa que defiende lo mismo; en ese caso hay una mutación que quita las dos capas.

## Uso (desde la raíz)

```bash
docker compose up -d db                               # las suites de backend usan PostgreSQL
python3 scripts/mutations/run.py --list               # suites y mutaciones
python3 scripts/mutations/run.py                      # todas menos ui-e2e
python3 scripts/mutations/run.py bookings secrets     # solo algunas
```

**Suite `ui-e2e`.** Necesita la UI servida por Vite, para que cada mutación se recargue al momento; una imagen Docker no reflejaría el cambio. Se prepara así:

```bash
KEEP=1 ./scripts/e2e.sh                                               # deja el stack tp-e2e en marcha
(cd frontend && API_URL=http://localhost:18000 npx vite --port 5174)  # en otra terminal
python3 scripts/mutations/run.py ui-e2e                               # E2E_BASE_URL=http://localhost:5174 por defecto
COMPOSE_PROJECT_NAME=tp-e2e docker compose down -v                    # al terminar
```

**Seguridad del runner**
- Se niega a mutar un archivo con cambios sin confirmar.
- Restaura siempre el original, también con Ctrl+C.
- Devuelve código 1 si alguna mutación sobrevive o ya no encaja con el código (`PATRÓN NO ENCONTRADO`).
- Tarda unos 15–20 s por mutación de backend (unos 15 min en total) y menos de 2 s por mutación de Vitest.

## Archivos

- `run.py`: el runner.
- `suites.py`: las mutaciones, como fragmentos exactos del código actual. Si el código cambia, la mutación avisa en lugar de saltarse en silencio.

**Origen de las mutaciones**
- Se copiaron de los scripts que se usaron durante el desarrollo: `mutate.py` (reservas), `mutate_docs.py`, `mutate_mcp.py` y `mutate_ui.py`.
- `secrets` reúne las que se hicieron a mano con `sed` (secretos y reintento de Gemini).
- **No se conservaron** los scripts de las primeras historias (mock del proveedor y US-01 a US-06): se sobrescribieron fuera del repo. Sus resultados solo constan en los mensajes de commit.

## Resultados de la última ejecución

Ejecutadas el 2026-11-18 con este runner, **sin nada más en marcha**. Se comprobó después que `git diff -- backend/ frontend/src` estaba vacío y que no quedaban archivos `.orig`.

| Suite | Mutaciones | Detectadas | Supervivientes |
| --- | --- | --- | --- |
| `bookings` | 16 (+1 el 2026-11-18) | 16 (+1) | — |
| `documents` | 12 | 11 | "sin deduplicar peticiones en curso" (ver abajo) |
| `mcp` | 16 | 16 | — |
| `secrets` | 7 | 7 | — |
| `ui-unit` (Vitest) | 11 | 6 | releer al reconectar, reaplicar con la versión vieja, ignorar eventos de reserva, botones para todos los roles, enviar términos distintos de los revisados |
| `ui-e2e` (Playwright) | 11 | 10 | "unknown parece confirmada" |

**Lectura de las supervivientes**

- **Documentos, "sin deduplicar peticiones en curso".** Es defensa en profundidad. Sin la comprobación de la aplicación, el índice único parcial de la migración 0005 (un documento activo por versión) provoca `Conflict`, y la rama `except` devuelve el documento en curso: el comportamiento no cambia. La mutación que quita **las dos capas** ("sin deduplicar ni en la app ni al chocar con el índice") sí se detecta.
- **UI.** Las dos capas se complementan, y **las 11 mutaciones las detecta al menos una**:
  - el e2e detecta las del flujo con el backend real: releer al reconectar, reaplicar, eventos en vivo, roles y términos enviados;
  - Vitest detecta "unknown parece confirmada", un estado que el e2e no puede provocar desde el navegador.

**Correcciones hechas al ejecutar**

- **Parser SSE.** La mutación borraba una línea y dejaba un `else` colgando, así que se "detectaba" por un error de sintaxis y no por un test. Se reescribió para ser válida, y ahora la detectan 4 tests de Vitest y el e2e.
- **"Ignorar eventos de reserva" en el e2e.** El 2026-11-17 se detectó, pero el 2026-11-18 sobrevivió. El test volvía a abrir la pestaña Reservas después de confirmar la reserva en el proveedor, y esa relectura con `GET` podía ocultar la falta del evento en vivo según el momento. Se corrigió el test: la pestaña queda abierta mostrando `pending` antes de la confirmación. Desde entonces se detecta en 2 de 2 ejecuciones.

**Mutaciones añadidas el 2026-11-18** (ejecutadas con el runner después de cada commit, sin nada más en marcha):

| Suite | Mutación | Resultado |
| --- | --- | --- |
| `bookings` | reconciliación ignora `Retry-After` | Detectada (3 tests) |
| `tracing` | el repositorio no copia el id de la petición al job | Detectada (2 tests) |
| `tracing` | el worker no restaura el id del job | Detectada |
| `tracing` | el middleware no restaura el contexto | Detectada |
| `mcp` | verificador de fechas acepta todo | Detectada (5 tests) |
| `mcp` | verificador de restricciones acepta todo | Detectada (7 tests) |
| `mcp` | conflicto sin nombrar los ítems | Detectada (2 tests) |
| `mcp` | verificador de cambios nunca detecta | Detectada (4 tests) |
| `mcp` | toda oferta cuenta como propuesta | Detectada (2 tests) |
| `mcp` | hueco sin atarlo a las noches | Detectada (2 tests) |
| `mcp` | hueco sin palabra de ausencia | Detectada (2 tests) |
| `mcp` | ausencia y alojamiento en frases distintas | Detectada (1 test). Sobrevivía en la primera prueba manual y se cerró con un caso negativo nuevo antes del commit `c0293c7` |
| `mcp` | huecos sin comprobar cambios | Detectada (1 test) |

Las 4 últimas son del verificador de huecos y se ejecutaron con el runner después del commit `c0293c7`.

**Total:** 75 mutaciones distintas (62 + 13). Solo una no la detecta ninguna capa de tests, la de defensa en profundidad de documentos, y quitar sus dos capas sí se detecta.
