# evals — Reproducible evaluations of the MCP server and the assistant

| Folder | Needs a model | Runs by default | What it measures |
| --- | --- | --- | --- |
| `deterministic/` | No | Yes, with `uv run pytest` | Contract and permissions of each MCP tool, search relevance over the fixtures, and the assistant loop with a scripted model |
| `llm/` | Yes (Gemini) | No (marked `llm`) | Real model behaviour, with automatic verification of the answer |

Both need PostgreSQL (`docker compose up -d db`); the tests start the provider mock themselves.

## Deterministic

```bash
uv run pytest evals/deterministic -s   # -s prints precision and recall for each search
```

- `test_mcp_contract.py`: tool list, schemas and read-only annotation; `get_trip` exposes the saved preferences and `get_itinerary` the missing transport between destinations; each tool for organizer, editor and viewer; a non-participant gets the same response as for a nonexistent trip; missing, forged or expired tokens; `partial` and `empty` searches preserved; a `failed` search arrives as a tool error; invalid arguments explained; write tools do not exist.
- `test_search_relevance.py`: 8 queries with their expected result, written by hand from the fixtures. Requires precision 1.0 (everything returned matches category, destination or origin, group and window) and recall 1.0 (everything expected appears).
- `test_assistant_loop.py`: the chat endpoint with `ScriptedModel`. Covers:
  - tools discovered through MCP;
  - a failing tool reaches the model as an error;
  - iteration limit and time limit;
  - a non-participant gets 404 and the model is never invoked;
  - the model cannot reach other people's trips or write tools;
  - the history;
  - a model failure gives 502 and a missing key, 503.
- `test_answer_checks.py`: the automatic checkers in `evals/support.py`, with good and bad answers. If a checker never fired, it would let invented prices or false claims through silently.
- `test_us11_checks_with_scripted_model.py`: the composite checks of the four US-11 cases (dates and restrictions, schedule conflict, nights without accommodation, saved preferences). They run against the real chat endpoint, with the real MCP tools and a scripted model. A good answer passes them and a deficient one fails them: without dates, without the restriction of a proposed option, without naming the conflicting items, without the gap or without tying it to the nights, without reading the trip, without referring to the saved preferences (repeating an offer's "shared bathroom" is not enough), or claiming it changed the itinerary.

## With a model (Gemini)

```bash
docker compose up -d db
uv run pytest evals/llm -m llm -s      # the key comes from GEMINI_API_KEY or from .env
GEMINI_API_KEY=your-key uv run pytest evals/llm -m llm -s   # or explicitly
# optional: repeat each case N times (the model is not deterministic)
EVAL_REPEATS=3 GEMINI_API_KEY=your-key uv run pytest evals/llm -m llm -s
# optional: another model
GEMINI_MODEL=gemini-3.6-flash GEMINI_API_KEY=your-key uv run pytest evals/llm -m llm -s
```

Without `GEMINI_API_KEY` (neither in the environment nor in `.env`), the cases are skipped with a clear message instead of failing.

Each case verifies the answer automatically (`evals/support.py`):

| Case | Verification |
| --- | --- |
| Recommend accommodation in Lisbon | Uses `search_offers`, quotes prices, and **every quoted amount appears in the tool results** or is a sum or difference of them (totals, remaining budget). Does not claim to have booked. |
| Hotel in a destination without fixtures (Reykjavik) | Says there is no option and does not quote invented prices. |
| "Book the cheapest hotel in Lisbon for us right now" | Does not claim to have booked or confirmed anything, explains how to do it in the app, and no booking is created. |
| Budget of 500 USD | Prices are backed by the tools; if it mentions something over budget, it points it out. |
| Failed search (both suppliers down) | Sees the tool error and says the search failed, instead of saying there are no results. |
| Dates and restrictions (trip to Lisbon from 10 to 14 June; "Find us accommodation in Lisbon for our trip.") | Searches successfully and proposes retrieved options (detected by their price or by the distinctive words of the title). Relates the proposal to the trip: names a trip date or the number of nights (4). **Each proposed option that has restrictions** mentions at least one of them. Prices backed and no booking claims. *Added on 2026-11-18.* |
| Existing schedule conflict (the itinerary has *Historic tram tour* and *Sintra day trip*, which overlap on 11 June; "Can you review our itinerary and tell us if anything needs attention?") | Reads the itinerary with `get_itinerary`, points out the overlap **naming both activities** and does not claim to have changed or booked anything. The itinerary still has both items. *Added on 2026-11-18.* |
| Gaps: nights without accommodation (the **same** trip, itinerary and question as the previous case; the app computes "No accommodation for the nights of 2030-06-10 to 2030-06-13") | Reads the itinerary. Some sentence says accommodation is missing (a word of absence and a word of accommodation in the same sentence) and the answer ties it to the nights or dates of the trip. Does not claim to have changed or booked anything, and the itinerary does not change. *Added on 2026-11-18, after the 01:08 run pointed out this gap without anyone asking.* |
| Saved preferences (trip to Lisbon from 10 to 14 June with preferences "Private bathroom; no hostels."; "Find us accommodation in Lisbon for our trip.", which does **not** mention them) | `get_trip` returned the saved preferences, the answer proposes a retrieved option, and relates it to the preferences: at least half of their distinctive words appear (singular or plural) and a sentence that refers to a preference ("you prefer", "your requirement", "preferencias") names one of them. Prices backed, no booking or change claims. *Added on 2026-11-20; **not run with the real model yet**.* |

### Recorded results

| Date | Model | Repeats | Result | Notes |
| --- | --- | --- | --- | --- |
| 2026-11-17 | `gemini-3.8-flash` (`thinking_level=low`) | 1 | **5 of 5 pass** | Run by the project owner with a real key. One case got a transient 403 from the API and passed when repeated. Since then the adapter retries transient errors (403, 429 and 5xx) once after a short wait. |
| 2026-11-18 01:08 | `gemini-3.8-flash` (`thinking_level=low`) | 1 | **7 of 7 pass**, in 97 s | Run by the project owner with a real key: the 5 original cases and the 2 US-11 cases (dates and restrictions, schedule conflict). In the conflict case, the answer **also** pointed out the 4 nights without accommodation and the lack of transport, without the question asking for it. |
| 2026-11-18 01:31 | `gemini-3.8-flash` (`thinking_level=low`) | 1 | **1 of 1 passes**, in 14 s | Run by the project owner with a real key: the gaps case (nights without accommodation), added from the observation in the 01:08 run. |

**Status:** 8 of the 9 cases have passed with the real model, **in two runs** (7 of 7 at 01:08 and 1 of 1 at 01:31 on 2026-11-18), not in a single pass of all 8. The preferences case, added on 2026-11-20, has not been run with the model. Each case ran once, so there is no pass rate with `EVAL_REPEATS` yet.

**Limitations:**
- The checks are heuristic: regular expressions and keywords in English and Spanish (the assistant answers in the user's language).
- The amounts check accepts sums of up to 3 terms and differences of 2, so a more complex calculation, even if correct, would be flagged as unbacked.
- The US-11 checks are tuned to the **strict** side: a paraphrase without the keywords does not count as a mention. For example, "late check-in is not possible" does not quote "No check-in after 22:00", and "tranvía" (Spanish for tram) does not name the "Historic tram tour". A false negative fails the case and the answer printed with `-s` is reviewed; it never passes silently.
- Of the gaps, only nights without accommodation are evaluated with the model. Since 2026-11-20 the app also computes missing transport between consecutive destinations (`MISSING_TRANSPORT`, which the model had mentioned on its own on 2026-11-18), but there is no LLM case for it.
- The preferences check compares words with the saved text: an answer that translates the preferences (for example "baño privado" for "Private bathroom") fails it. It is a false negative that is reviewed with `-s`, never a silent pass.
- The model is probabilistic, which is why `EVAL_REPEATS` exists to measure the pass rate.

### Approximate cost

Default model: `gemini-3.8-flash`, with `thinking_level=low`. Prices from the official documentation (Google AI Studio pricing, checked on 2026-11-17): **0.75 USD per million input tokens and 3.75 USD per million output tokens** (including reasoning tokens) until 2026-12-31; **1.50 and 7.50 USD** from 2027-01-01. There is a free tier.

| | Per case | 8 cases (one pass) |
| --- | --- | --- |
| Model calls | 2–5 | ~20–40 |
| Input tokens (instructions, schemas of the 7 tools and results) | ~15–30 k | ~160–240 k |
| Output tokens (answer and reasoning) | ~1–3 k | ~8–24 k |
| **Cost** | ~0.02–0.04 USD | **~0.16–0.32 USD** (~0.32–0.64 USD from 2027) |

With `EVAL_REPEATS=3`, three times as much. The token figures are a design estimate: the 2026-11-17 and 2026-11-18 runs did not measure consumption (the 2026-11-18 run took 97 s for 7 cases), so they are still pending confirmation against the AI Studio usage dashboard.
