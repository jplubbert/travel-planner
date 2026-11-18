# 0004. Offer snapshot in the item and freshness policy

- **Status:** Accepted
- **Date:** 2026-11-17
- **Related stories:** US-03, US-04, US-05, US-07 (and US-10, which shows the same data)

## Context

- Provider prices are **quotes** with `revision` and `validUntil`. They change, expire and do not hold inventory (contract `getOffer`, `searchOffers`).
- US-04 asks for the itinerary to show times and costs and to flag expired prices and unavailable options. Saved changes must still be there when the trip is reopened.
- US-05 asks to distinguish estimates from confirmed prices, and for confirmed prices to stay understandable even if the provider changes its prices.
- US-03 asks for the presentation to make freshness clear. The guide values "sensible reuse of results without presenting stale data as confirmed" and "justified performance trade-offs".
- The client (browser or assistant) is not a reliable source of prices.

We need to decide what an itinerary item stores about its offer and when the provider is queried.

## Options considered

### A. The item stores only `offerId` and reads the offer live on every read

- ✅ Always the latest.
- ❌ Every read of the itinerary and costs makes N calls to the provider: slow, sensitive to 429 and 503, and a provider outage leaves the itinerary without times or costs.
- ❌ The "estimated" cost changes without anyone deciding it, which makes totals irreproducible and loses the history that US-10 will show.

### B. Shared search cache (TTL) that the itinerary and costs read from

- ✅ Fewer calls.
- ❌ It must be invalidated and a TTL chosen. If it does not match `validUntil`, it presents an expired quote as current, or the other way round.
- ❌ A search is for a specific group and date window; reusing it for another query is incorrect.
- ❌ It is another piece of state to keep consistent across replicas. There is no measured performance problem that justifies it.

### C. The client sends the offer (or its price) when adding it

- ✅ No extra calls.
- ❌ The server cannot trust a price sent by the client: costs and the booking's `expectedPrice` would depend on it.

### D. Snapshot of the offer in the item, taken from the provider, refreshed on demand (chosen)

## Decision

1. **When an item is added, the backend fetches the offer from the provider** (`GET /offers/{offerId}`) and stores a **full snapshot** in the item:
   - typed columns for the logic (price `numeric(12,2)`, `revision`, `available`, `validUntil`, schedule, travellers, restrictions);
   - `offer_snapshot`, the exact JSON in contract format, which preserves local offsets. `timestamptz` stores the instant but not the offset, and local dates matter to check whether an item falls within the trip;
   - `quoted_at`, the moment it was obtained.

   The request body only accepts `offerId` (and `version`); a `price` sent by the client gives `422`.
2. **Searches are not cached.** Every search queries the provider and returns `searchedAt`, `receivedAt` and `validUntil` per offer (US-03).
3. **Itinerary and cost reads do not call the provider.** They use the snapshot and compute at read time:
   - `quote.expired` / `PRICE_EXPIRED`, if `now ≥ validUntil`;
   - `OFFER_UNAVAILABLE`, based on `available` in the latest quote.

   Any amount without a confirmed booking is marked as `estimate`.
4. **Refreshing is explicit and on demand:** `POST /trips/{id}/itinerary/items/{itemId}/refresh`. It replaces the snapshot with the provider's current quote.
   - It is a versioned mutation (ADR 0001) recorded in the history with a summary of what changed (price, availability, schedule, validity).
   - If the quote did not change, it saves nothing and does not bump the version.
   - If the provider fails, the previous snapshot is left intact.
5. **The confirmed price is something else.** The cost of an item with a `confirmed` booking uses the `accepted_price` and the accepted offer stored by the booking (ADR 0003), never the item's snapshot. Refreshing the snapshot afterwards does not change what was confirmed.
6. Before booking (US-07), the backend fetches the offer again, because the item's snapshot may be stale. The snapshot is for planning, not for accepting terms.

## Consequences

**Positive**

- Reading the itinerary and costs is fast, deterministic and works with the provider down. Estimates only change when an editor decides to refresh, and that change is recorded in the history.
- Freshness is explicit at three levels: `quotedAt`, `validUntil` and `expired`/`PRICE_EXPIRED`. Nothing stale is presented as current or as confirmed.
- The server never depends on prices sent by the client.

**Negative / risks**

- The displayed availability can be out of date, and this is stated explicitly ("as of quotedAt"). An option may have sold out without the itinerary knowing until someone refreshes it or tries to book.
- Refreshing is manual, item by item. A "refresh all" or a background refresh are possible improvements, which would have to respect the provider's rate limits.
- The snapshot duplicates provider data; this is intentional (history and reproducibility).

## Verification

- `tests/integration/test_us04_itinerary.py`:
  - the price comes from the provider and a price sent by the client is rejected;
  - refreshing records the change, saves nothing if the quote did not change and keeps the snapshot if the provider fails;
  - expired quotes and unavailable offers are flagged.
- `tests/integration/test_us05_costs.py`: the confirmed price survives a provider price change followed by a refresh.
- `tests/integration/test_persistence.py`: the local offset survives the round trip through the database, and migration 0003 backfills the snapshot of existing items.
