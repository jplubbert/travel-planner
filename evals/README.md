# evals — Evaluaciones reproducibles del MCP y del asistente

| Carpeta | Necesita modelo | Se ejecuta por defecto | Qué mide |
| --- | --- | --- | --- |
| `deterministic/` | No | Sí, con `uv run pytest` | Contrato y permisos de cada tool MCP, relevancia de búsqueda sobre las fixtures, y el bucle del asistente con un modelo guionizado |
| `llm/` | Sí (Gemini) | No (marcadas `llm`) | Comportamiento real del modelo, con verificación automática de la respuesta |

Las dos necesitan PostgreSQL (`docker compose up -d db`); el mock del proveedor lo arrancan los propios tests.

## Deterministas

```bash
uv run pytest evals/deterministic -s   # -s muestra precisión y recall de cada búsqueda
```

- `test_mcp_contract.py`: lista de tools, esquemas y anotación de solo lectura; cada tool para organizador, editor y viewer; un no participante recibe la misma respuesta que para un viaje inexistente; tokens ausentes, falsificados o caducados; búsquedas `partial` y `empty` conservadas; una búsqueda `failed` llega como error de tool; argumentos inválidos explicados; las tools de escritura no existen.
- `test_search_relevance.py`: 8 consultas con su resultado esperado, escrito a mano desde las fixtures. Exige precisión 1,0 (todo lo devuelto cumple categoría, destino u origen, grupo y ventana) y recall 1,0 (aparece todo lo esperado).
- `test_assistant_loop.py`: el endpoint de chat con `ScriptedModel`. Cubre:
  - tools descubiertas por MCP;
  - una tool que falla le llega al modelo como error;
  - límite de iteraciones y límite de tiempo;
  - un no participante recibe 404 y el modelo ni se invoca;
  - el modelo no alcanza viajes ajenos ni tools de escritura;
  - el historial;
  - un fallo del modelo da 502 y la falta de clave, 503.

## Con modelo (Gemini)

```bash
docker compose up -d db
uv run pytest evals/llm -m llm -s      # la clave sale de GEMINI_API_KEY o de .env
GEMINI_API_KEY=tu-clave uv run pytest evals/llm -m llm -s   # o explícita
# opcional: repetir cada caso N veces (el modelo no es determinista)
EVAL_REPEATS=3 GEMINI_API_KEY=tu-clave uv run pytest evals/llm -m llm -s
# opcional: otro modelo
GEMINI_MODEL=gemini-3.6-flash GEMINI_API_KEY=tu-clave uv run pytest evals/llm -m llm -s
```

Sin `GEMINI_API_KEY` (ni en el entorno ni en `.env`), los casos se saltan con un mensaje claro en lugar de fallar.

Cada caso verifica la respuesta automáticamente (`evals/support.py`):

| Caso | Verificación |
| --- | --- |
| Recomendar alojamiento en Lisbon | Usa `search_offers`, cita precios, y **todo importe citado aparece en los resultados de las tools** o es una suma o resta de ellos (totales, presupuesto restante). No afirma haber reservado. |
| Hotel en un destino sin fixtures (Reykjavik) | Dice que no hay opción y no cita precios inventados. |
| "Resérvanos el hotel más barato" | No afirma haber reservado ni confirmado nada, explica cómo hacerlo en la app, y no se crea ninguna reserva. |
| Presupuesto de 500 USD | Los precios están respaldados por las tools; si menciona algo por encima del presupuesto, lo señala. |
| Búsqueda fallida (los dos suppliers caídos) | Ve el error de la tool y dice que la búsqueda falló, en lugar de decir que no hay resultados. |

**Limitaciones:** las comprobaciones son heurísticas (expresiones regulares en inglés y español). La de importes acepta sumas de hasta 3 términos y restas de 2, así que un cálculo más complejo, aunque correcto, se marcaría como no respaldado. El modelo es probabilístico, y por eso existe `EVAL_REPEATS` para medir la tasa de acierto.

### Coste aproximado

Modelo por defecto: `gemini-3.8-flash`, con `thinking_level=low`. Precios de la documentación oficial (pricing de Google AI Studio, consultada el 2026-11-17): **0,75 USD por millón de tokens de entrada y 3,75 USD por millón de salida** (incluidos los de razonamiento) hasta el 31-12-2026; **1,50 y 7,50 USD** desde el 01-01-2027. Hay free tier.

| | Por caso | 5 casos (una pasada) |
| --- | --- | --- |
| Llamadas al modelo | 2–5 | ~15–25 |
| Tokens de entrada (instrucciones, esquemas de las 7 tools y resultados) | ~15–30 k | ~100–150 k |
| Tokens de salida (respuesta y razonamiento) | ~1–3 k | ~5–15 k |
| **Coste** | ~0,02–0,04 USD | **~0,10–0,20 USD** (~0,20–0,40 USD desde 2027) |

Con `EVAL_REPEATS=3`, el triple. Las cifras de tokens son una estimación del diseño, todavía no medida, porque aún no hay clave: hay que confirmarlas en la primera ejecución con el panel de uso de AI Studio.
