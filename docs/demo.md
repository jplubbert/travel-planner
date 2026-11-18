# Demo script (10–15 minutes)

Two windows are used: the browser at http://localhost:5173 and a terminal at the repo root. Clicks refer to buttons and labels visible in the UI.

## 0. Setup (before starting, ~2 min)

```bash
cp .env.example .env              # for step 7, set GEMINI_API_KEY=... in .env
docker compose down -v            # optional: start from scratch (DELETES the stack's local data)
docker compose up --build -d --wait
export B=http://localhost:4010    # provider mock (/v1 is the contract; /_control, demo only)
curl -s -X DELETE $B/_control/scenario   # no failure scenarios active
```

Open http://localhost:5173. The **Test user** selector is at the top right.

## 1. Full flow (~4 min)

1. **Test user:** `ana`. Click **New trip**. Fill in:
   - Name `Lisbon demo`;
   - Destinations `Lisbon`;
   - Start date June 10, 2030, End date June 14, 2030;
   - Travelers `2`;
   - Budget `3000.00`.

   Click **Create**. The header shows the role, `version 1` and `status: live` (the SSE channel).
   - *To show validation:* repeat with an empty budget; `422 VALIDATION_ERROR` appears with the field.
2. **Participants:**
   - in **Add participant** choose Beto, Role `editor`, and click **Add**;
   - repeat with Carla, Role `viewer`.
3. **Search:** Category `Accommodation`, Destination `Lisbon`, click **Search**. Point out:
   - the green notice "Search complete";
   - "received N s ago" (freshness);
   - "estimated price" and "valid until";
   - the restrictions;
   - *Three-night Belém boutique hotel* marked **unavailable**.
4. Click **Add to itinerary** on *Four-night central hotel stay* (670.00 USD).
5. *(Optional, schedule conflict)* Search: Category `Activity`, Start date and End date June 11, 2030, **Search**. Add *Historic tram tour* and *Sintra day trip*, which overlap on June 11. In **Itinerary** both show **Conflict: Overlaps with 'Sintra day trip'.** (and vice versa).
6. **Itinerary:** price "estimate", "not booked", ↑ ↓ arrows to reorder, **Refresh quote** and **Missing information** if any.
7. **Costs:** Estimated (unconfirmed) 670.00 USD, Confirmed by the provider 0.00 USD, Remaining.
8. **Bookings:**
   - click **Review terms** on the hotel: price, quote, validity and restrictions;
   - click **Confirm booking for 670.00 USD**;
   - the card shows **"Booked: confirmed by the provider"**, the accepted price and the provider reference.
9. **Costs:** Confirmed by the provider 670.00 USD.
10. **Documents:**
    - click **Generate document for version N**;
    - the status goes from "Queued" or "Generating" to **Ready** without reloading (SSE);
    - click **Download** and open the HTML: itinerary, costs, booking statuses, version and snapshot time.

## 2. Permissions (~1.5 min)

1. Switch **Test user** to `carla` and open *Lisbon demo*. It shows `your role: viewer` and there are no edit buttons.
2. **Search** for accommodation in Lisbon: **Add to itinerary** does not appear.
3. Tick **"Show actions my role does not allow"** and press **Add to itinerary**. `403 FORBIDDEN` appears with "This action requires the editor role; you are a viewer of this trip.": the 403 is shown as returned by the backend (status, code and message).
4. Switch to `dani` and reload the trip URL (`#/trips/<id>`). `404 TRIP_NOT_FOUND` appears, the same response as for a trip that does not exist.
5. *(Optional)* The MCP tools apply the same permission. See `evals/deterministic/test_mcp_contract.py` (`uv run pytest evals/deterministic -k not_found`).

## 3. 409 conflict with reapply (~2 min)

The live channel updates the version in under a second, so to trigger the conflict by hand it has to be cut in Beto's window.

1. Open a private window (so it keeps its own user) with `beto` on *Lisbon demo*.
2. In DevTools: **Network** → ⋮ menu → **More tools** → **Network request blocking**. Add the pattern `*events*` and reload. The header stays at "reconnecting…".
3. In `ana`'s window: **Participants** → **Edit trip details**, change the budget to `2500.00` and **Save** (version +1).
4. In `beto`'s window: **Search** for accommodation in Lisbon and **Add to itinerary** on *Four-night Alfama apartment*. The **409 TRIP_VERSION_CONFLICT** panel appears:
   - "Your change (…) was based on version N; the current one is N+1";
   - the list of changes (`vN+1 · ana · … · Updated budget`);
   - the header already shows the 2500.00 budget.
5. Click **Reapply “add '…'” on version N+1**. It is saved, the version goes up and the item appears in **Itinerary**.
6. Remove the block in DevTools. The status goes back to "live" and the view **is re-read with GET** (`ready`): any change made meanwhile shows up without relying on the lost events.

Alternative without DevTools: see the raw `409` with `curl` (sending an old version):

```bash
curl -s -X PATCH -H 'X-User-Id: ana' -H 'Content-Type: application/json' \
  http://localhost:8000/trips/<tripId> -d '{"version": 1, "name": "x"}' | python3 -m json.tool
```

## 4. Partial, failed and empty search (~1.5 min)

```bash
curl -s -X POST $B/_control/scenario -H 'Content-Type: application/json' -d '{"supplierId":"supplier-b","mode":"unavailable"}'
```

1. **Search** for accommodation in Lisbon. The amber notice **"Partial results"** appears, with "No answer from supplier-b", and supplier-a's offers are still visible and usable.

```bash
curl -s -X POST $B/_control/scenario -H 'Content-Type: application/json' -d '{"supplierId":"supplier-a","mode":"unavailable"}'
```

2. **Search** again. The red notice **"Search failed: this is not the same as no options"** appears, with the code, and no offers.

```bash
curl -s -X DELETE $B/_control/scenario
```

3. Destination `Reykjavik` and **Search**. The neutral notice **"No options"** appears.

Variant: `"mode":"rate_limited"` shows `Retry-After`.

## 5. Booking with a lost response, reconciled (~3 min)

The API dies while waiting for the provider's response. The provider did create the booking. The app does not present it as booked, and the worker reconciles it by reference, without duplicating it.

1. As `ana`, on a Lisbon trip, **add** *Four-night Baixa hostel beds* (or any unbooked **supplier-a** offer). Go to **Bookings** → **Review terms**. **Do not confirm yet.**
2. In the terminal, make supplier-a take 4 s on every operation. That is less than the app's timeout (5 s), so the preliminary offer lookup succeeds:

   ```bash
   curl -s -X POST $B/_control/scenario -H 'Content-Type: application/json' \
     -d '{"supplierId":"supplier-a","mode":"timeout","delaySeconds":4}'
   ```
3. Click **Confirm booking** and, as soon as it is pressed, run:

   ```bash
   sleep 6 && docker compose kill api   # between the preliminary lookup (4 s) and the POST response (8 s)
   ```

   The UI shows a network error or a `502` from the proxy.
4. Start the API again and remove the delay:

   ```bash
   curl -s -X DELETE $B/_control/scenario
   docker compose start api
   ```
5. Reload. In **Bookings**, the card says **"Sending to the provider (not booked yet)"**, `booked` is false and it is not counted under Confirmed in **Costs**.
6. Wait about 60 s (`BOOKING_SUBMITTED_STALE_SECONDS`) without touching anything. The card turns into **"Booked: confirmed by the provider"** on its own (SSE).
7. See how it was resolved:

   ```bash
   docker compose logs worker | grep -E "interrupted|by-reference|sync_booking"
   # … 1 interrupted booking submission(s) scheduled for reconciliation
   # … GET …/v1/bookings/by-reference/tp-… 200
   # … sync_booking -> succeeded
   ```

   The card's **Our reference** (`tp-…`) is the one that was looked up. The provider has a single booking for it:

   ```bash
   curl -s -H 'Authorization: Bearer travel-demo-token' $B/v1/bookings/by-reference/<tp-...>
   ```

## 6. Document regenerated after deleting the file (~1.5 min)

1. **Documents** should have one **Ready** (if not, **Generate** and wait).
2. Delete the file underneath the application:

   ```bash
   docker compose exec api find /data/documents -name '*.html'
   docker compose exec api rm /data/documents/<tripId>/<documentId>.html
   ```
3. Click **Download**. `409 DOCUMENT_FILE_UNAVAILABLE` appears: "The stored file is missing or does not match its recorded checksum, so it was not delivered. It is being regenerated…". No broken file is delivered.
4. The status goes to "Queued" and then to **Ready** without reloading. **Download** works and the `sha256` is the same as before, because it is regenerated from the immutable snapshot, not from the current trip.

## 7. Assistant (~2 min)

It needs `GEMINI_API_KEY` in `.env` and `docker compose up -d api` so the API reads it. Without a key, the assistant answers `503 ASSISTANT_UNAVAILABLE` and everything else works.

1. As `carla` (viewer), in **Assistant**: *"Find accommodation in Lisbon for us"* (the question the candidate used in their test on 2026-11-17, asked in Spanish, on a Lisbon trip from June 10 to 14; the assistant replies in the user's language). Point out:
   - the answer quotes prices;
   - under **Tools used**, the MCP calls (`get_trip`, `get_costs`, `search_offers`…) with arguments and results, which is where the amounts come from.
2. *"Book the cheapest hotel for us now."* It does not book: it explains that it is read-only and how to do it in the app. No write tool exists.
3. *(Optional)* With supplier-a and supplier-b `unavailable` (step 4): *"Find accommodation in Lisbon."* The assistant says the search failed, not that there are no options.

## What has been rehearsed from this script

**Incident and repetition.**
- A first rehearsal of steps 1.5, 5 and 6 (2026-11-18, ~00:00) was run on a stack built **while the mutation runner had mutated code in place** (`if False:` in `reservations.py`). That rehearsal does not count.
- It was repeated afterwards:
  - with the code restored (empty `git diff` for `backend/` and `frontend/src`, no `.orig` files);
  - with the full suite green;
  - with `tp-demo` rebuilt from scratch (`docker compose build --no-cache`, fresh volumes);
  - checking that the 59 backend `.py` files inside the `api` and `worker` images are byte-for-byte identical to the repo.
- The results below are from that repetition. Since then, `CLAUDE.md` forbids building images or running tests while mutations are running.

| Step | How it was verified | Status |
| --- | --- | --- |
| 1. Full flow (except the optional conflict 1.5) | `frontend/e2e/flow.spec.ts` "full flow", with the same clicks, on Docker from scratch (`scripts/e2e.sh`, 5 of 5 on 2026-11-18) | Run (automated) |
| 1.5 Schedule conflict | Backend in `tests/integration/test_us04_itinerary.py`. Repeated via the API on the clean stack (2026-11-18 00:19): both items get `SCHEDULE_CONFLICT` ("Overlaps with 'Sintra day trip'." and vice versa). An empty budget gives `422` | Via the API, run. In the UI **not rehearsed** |
| 2. Permissions 403 and 404 | e2e "permissions" | Run (automated) |
| 3. 409 with reapply and re-read on reconnect | e2e "version 409", with the channel cut by Playwright instead of DevTools | Run (automated). Blocking with DevTools **not rehearsed by hand** |
| 4. Partial, failed and empty | e2e "partial and failed searches" | Run (automated) |
| 5. Lost response | Repeated with `curl` instead of the click, on the clean stack (2026-11-18 00:17–00:19). The booking `POST` got a `502` from the proxy when the API was killed 6 s after sending it. After the restart, the booking was `submitted` (`booked=false`, `nextStep` "Being sent to the provider…"). After ~56 s the worker logged "1 interrupted booking submission(s) scheduled for reconciliation", queried `GET /v1/bookings/by-reference/tp-…` (`200`) and the booking became `confirmed` with `booking-000001`, the only booking in that freshly created mock. That the change reaches the card live is covered by the `pending` booking e2e | Run via the API. The UI click **not rehearsed** |
| 6. Document regenerated | Repeated with `curl` on the clean stack (2026-11-18 00:19):<br>• document `complete` (v2, sha256 `129388c8e155…`);<br>• non-participant: `404`;<br>• after `rm` of the file, the download gives `409 DOCUMENT_FILE_UNAVAILABLE` (`FILE_MISSING`) and the status goes to `waiting`;<br>• within ~1 s it is back to `complete` with the **same** sha256;<br>• the download gives `200` and the downloaded file has that hash | Run via the API. The UI click **not rehearsed** |
| 7. Assistant with a key | **Run by the candidate from the UI with a real key** on 2026-11-17 at 23:49, with `gemini-3.8-flash` (result reported by the candidate; not reproduced by the agent).<br>• Lisbon trip from June 10 to 14, 2030; question "Find accommodation in Lisbon for us" (asked in Spanish).<br>• It answered with 3 options priced from the tools (225.60, 670.00 and 600.00 USD) and related each one to the nights and the remaining budget.<br>• It ruled out Belém for not covering the first night and being unavailable, stated the prices' validity and made clear it cannot book.<br>• It used 3 tools.<br>The model evaluations also gave 5 of 5 the same day | Run (by the candidate) |
