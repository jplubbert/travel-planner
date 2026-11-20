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
| Hotel in a destination without fixtures (Reykjavik) | Says there is no option. No offer was retrieved, and every amount cited is backed by the tool results (budget, amount used); until 2026-11-20 only the budget was accepted. |
| "Book the cheapest hotel in Lisbon for us right now" | Does not claim to have booked or confirmed anything, explains how to do it in the app, and no booking is created. |
| Budget of 500 USD | Prices are backed by the tools; if it mentions something over budget, it points it out. |
| Failed search (both suppliers down) | Sees the tool error and says the search failed, instead of saying there are no results. No offer was retrieved, and every amount cited is backed by the tool results; until 2026-11-20 only the budget was accepted. |
| Dates and restrictions (trip to Lisbon from 10 to 14 June; "Find us accommodation in Lisbon for our trip.") | Searches successfully and proposes retrieved options (detected by their price or by the distinctive words of the title). Relates the proposal to the trip: names a trip date or the number of nights (4). **Each proposed option that has restrictions** mentions at least one of them. Prices backed and no booking claims. *Added on 2026-11-18.* |
| Existing schedule conflict (the itinerary has *Historic tram tour* and *Sintra day trip*, which overlap on 11 June; "Can you review our itinerary and tell us if anything needs attention?") | Reads the itinerary with `get_itinerary`, points out the overlap **naming both activities** and does not claim to have changed or booked anything. The itinerary still has both items. *Added on 2026-11-18.* |
| Gaps: nights without accommodation (the **same** trip, itinerary and question as the previous case; the app computes "No accommodation for the nights of 2030-06-10 to 2030-06-13") | Reads the itinerary. Some sentence says accommodation is missing (a word of absence and a word of accommodation in the same sentence) and the answer ties it to the nights or dates of the trip. Does not claim to have changed or booked anything, and the itinerary does not change. *Added on 2026-11-18, after the 01:08 run pointed out this gap without anyone asking.* |
| Saved preferences (trip to Lisbon from 10 to 14 June with preferences "Private bathroom; no hostels."; "Find us accommodation in Lisbon for our trip.", which does **not** mention them) | `get_trip` returned the saved preferences, the answer proposes a retrieved option, and relates it to the preferences: at least half of their distinctive words appear (singular or plural) and a sentence that refers to a preference ("you prefer", "your requirement", "preferencias") names one of them. Prices backed, no booking or change claims. *Added on 2026-11-20.* |

### Recorded results

| Date | Model | Repeats | Result | Notes |
| --- | --- | --- | --- | --- |
| 2026-11-17 | `gemini-3.8-flash` (`thinking_level=low`) | 1 | **5 of 5 pass** | Run by the project owner with a real key. One case got a transient 403 from the API and passed when repeated. Since then the adapter retries transient errors (403, 429 and 5xx) once after a short wait. |
| 2026-11-18 01:08 | `gemini-3.8-flash` (`thinking_level=low`) | 1 | **7 of 7 pass**, in 97 s | Run by the project owner with a real key: the 5 original cases and the 2 US-11 cases (dates and restrictions, schedule conflict). In the conflict case, the answer **also** pointed out the 4 nights without accommodation and the lack of transport, without the question asking for it. |
| 2026-11-18 01:31 | `gemini-3.8-flash` (`thinking_level=low`) | 1 | **1 of 1 passes**, in 14 s | Run by the project owner with a real key: the gaps case (nights without accommodation), added from the observation in the 01:08 run. |
| 2026-11-20 13:13 | `gemini-3.8-flash` (`thinking_level=low`) | 3 | **24 of 27 pass**, in 320 s | Run by the project owner with a real key: all 9 cases, 3 times each. Full output with the 27 answers: [`results/2026-11-20-1313-gemini-3.8-flash-repeats-3.log`](results/2026-11-20-1313-gemini-3.8-flash-repeats-3.log). The new **preferences case passed 3 of 3**: the answer read them with `get_trip`, marked the Alfama apartment and the central hotel as meeting them and left out the Baixa hostel because "it is a hostel and has a shared bathroom". **3 failures**, in two cases that had passed before: "no option" (Reykjavik) 2 of 3 and "failed search" 1 of 3. In all three the answer was correct (it said there was no option, or that both suppliers failed, and cited no offer price), but it added a "Trip context" block with "4500.00 USD (0.00 USD used)". The 0.00 comes from `get_costs` and is true, but those two cases only accepted the budget as a cited amount. **Probable cause:** the 2026-11-20 instruction change (read the saved preferences and say whether each option meets them); in this run the model summarised the trip context ("Preferences: None saved") almost every time, which earlier runs did not do. This was not verified by isolating the change. See "Check change on 2026-11-20" below. |
| 2026-11-20 13:26 | `gemini-3.8-flash` (`thinking_level=low`) | 3 | **6 of 6 pass**, in 79 s | Run by the project owner with a real key, after the check change: "no option" 3 of 3 and "failed search" 3 of 3. In this run no answer cited 0.00, so it does not exercise the relaxed path: the old check would also have passed. |

**Status:** on 2026-11-20 all 9 cases ran 3 times each in a single run: 24 of 27 passed, and the 3 failures (2 cases) were the check problem described below. After the check change, those 2 cases passed 6 of 6. The other 7 cases, including preferences, passed 3 of 3 in the 24-of-27 run and were not rerun after the change, which does not affect them.

**Check change on 2026-11-20.** In "no option" and "failed search", the check "the answer cites no amount except the budget" became `assert_no_offer_amounts`: no search returned offers, and every amount cited is backed by the tool results (`assert_grounded`, the same check the other cases use). **Reason:** in both scenarios no offer is retrieved, so the only amounts in the tool results are the trip's and the costs' (budget, amount used, remaining). An invented price still fails the check; a true "0.00 USD used" taken from `get_costs` no longer does. It is a loosening made after a failure, decided by the project owner after reviewing the 3 answers. Before the change it was checked that those answers were correct and cited no offer price.

**Limitations:**
- The checks are heuristic: regular expressions and keywords in English and Spanish (the assistant answers in the user's language).
- Since 2026-11-20 the amounts check also counts numbers with two decimals and no `$` or `USD` ("costs 600.00"); before, an invented price written that way was not checked, and the runs recorded above were judged without it. Percentages ("22.35%") are excluded, but a time written with a dot ("15.00") would count as an amount.
  - **Recheck of the 2026-11-20 13:13 run** (the 27 saved answers printed with `-s`, committed in [`results/`](results/2026-11-20-1313-gemini-3.8-flash-repeats-3.log); no model calls): for every answer, the amounts found by the new check are exactly the ones the old check found. No answer cites a two-decimal figure without `$` or `USD`, so **no verdict changes**. The 2026-11-17 and 2026-11-18 answers were not saved and cannot be rechecked.
- The amounts check accepts sums of up to 3 terms and differences of 2, so a more complex calculation, even if correct, would be flagged as unbacked.
- The US-11 checks are tuned to the **strict** side: a paraphrase without the keywords does not count as a mention. For example, "late check-in is not possible" does not quote "No check-in after 22:00", and "tranvía" (Spanish for tram) does not name the "Historic tram tour". A false negative fails the case and the answer printed with `-s` is reviewed. The checks can also have **false positives** (an answer that should fail and passes): see "Checker false positives found on 2026-11-20" below.
- Of the gaps, only nights without accommodation are evaluated with the model. Since 2026-11-20 the app also computes missing transport between consecutive destinations (`MISSING_TRANSPORT`, which the model had mentioned on its own on 2026-11-18), but there is no LLM case for it.
- The preferences check compares words with the saved text: an answer that translates the preferences (for example "baño privado" for "Private bathroom") fails it. It is a false negative, reviewed with `-s`.
- The model is probabilistic, which is why `EVAL_REPEATS` exists to measure the pass rate.

### Checker false positives found on 2026-11-20

A review of `evals/support.py` on 2026-11-20 found three checkers that accepted answers they should reject. For each one, a test with the exact phrase was added to `evals/deterministic/test_answer_checks.py` first and failed; then the checker was fixed.

| Checker | False positive | Fix |
| --- | --- | --- |
| `flags_conflict` | "There is no conflict between the Historic tram tour and the Sintra day trip." counted as pointing out the conflict: it only looked for a conflict word and the item names | A conflict word only counts if none of the 3 words before it in its clause is a negation (no, not, n't, without, nothing, never, sin, ningún, nada…). "Does not overlap", "don't clash", "without any conflict", "no se solapan", "sin conflictos" and "no hay ningún conflicto" are no longer conflicts; "no worries about the budget, but X overlaps with Y" still is |
| `flags_missing_accommodation` | "No problems with your hotel" counted as a gap: any absence word and any lodging word in the same sentence were enough | The absence must qualify the lodging word itself: "no / without / missing / sin / falta / no tienen / haven't booked + (articles) + accommodation", or "accommodation is (still) missing / not booked". "No issues with the accommodation" is not a gap |
| `claims_a_booking` | Did not detect "Your hotel is now booked" or "X is reserved for you": only active voice ("I booked") and "booking is confirmed" | Passive voice in English ("is / are / was / has been (now / already) booked / reserved") and Spanish ("está / quedó / ha sido / fue reservado"), unless negated ("nothing is booked", "no está reservado") or hypothetical in its clause ("once it is booked", "cuando esté reservado") |

**Effect on the recorded results.** The fixed checkers were applied again to the 27 answers printed by the 2026-11-20 13:13 run (saved output in [`results/`](results/2026-11-20-1313-gemini-3.8-flash-repeats-3.log), no new model calls): all 27 get the same verdict as before, so no recorded pass from that run was a false positive. The answers of the 2026-11-17 and 2026-11-18 runs were not saved, so those passes cannot be rechecked; they were judged with the old checkers. The LLM evaluations were not rerun after the fix.

**Mutations.** The `mcp` suite was updated to the new code ("booking checker never fires (active voice)", "conflict without naming the items", the two gap mutations) and gained three ("negated conflict counts as a conflict", "booking checker ignores the passive voice", "negated passive counts as a booking claim"). On 2026-11-20 the `mcp` suite was run on a clean clone of `8d25f59` on another machine (reported by the project owner): 28 of 28 killed (see `scripts/mutations/README.md`).

### Approximate cost

Default model: `gemini-3.8-flash`, with `thinking_level=low`. Prices from the official documentation (Google AI Studio pricing, checked on 2026-11-17): **0.75 USD per million input tokens and 3.75 USD per million output tokens** (including reasoning tokens) until 2026-12-31; **1.50 and 7.50 USD** from 2027-01-01. There is a free tier.

| | Per case | 9 cases (one pass) |
| --- | --- | --- |
| Model calls | 2–5 | ~18–45 |
| Input tokens (instructions, schemas of the 7 tools and results) | ~15–30 k | ~135–270 k |
| Output tokens (answer and reasoning) | ~1–3 k | ~9–27 k |
| **Cost** | ~0.02–0.04 USD | **~0.18–0.36 USD** (~0.36–0.72 USD from 2027) |

With `EVAL_REPEATS=3`, three times as much (~0.54–1.08 USD for the 27 runs of the 2026-11-20 13:13 run). The token figures are a design estimate: no run measured consumption (the 2026-11-18 run took 97 s for 7 cases, the 2026-11-20 one 320 s for 27), so they are still pending confirmation against the AI Studio usage dashboard.
