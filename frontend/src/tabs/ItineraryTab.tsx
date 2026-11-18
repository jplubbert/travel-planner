import { useState } from "react";

import { Badge, ErrorBox, Notice } from "../components";
import { BOOKING, CATEGORY_LABEL, dateTime, usd } from "../format";
import { useLoad, useTrip } from "../TripView";
import type { Item, Itinerary, Refreshed } from "../types";

const FLAG_TONE: Record<string, "bad" | "warn"> = { SCHEDULE_CONFLICT: "bad", OFFER_UNAVAILABLE: "bad" };

export function ItineraryTab() {
  const { api, trip, ticks } = useTrip();
  const itinerary = useLoad(() => api.get<Itinerary>(`/trips/${trip.id}/itinerary`),
    [trip.id, ticks.trip, ticks.booking]);
  const [refreshNote, setRefreshNote] = useState<Refreshed | null>(null);
  const data = itinerary.data;

  return (
    <div>
      <ErrorBox error={itinerary.error} />
      {!data ? <p>Loading itinerary…</p> : (
        <>
          {data.missing.length > 0 && (
            <Notice tone="warn">
              <strong>Missing information</strong>
              <ul>{data.missing.map((g) => <li key={g.code + g.message}>{g.message}</li>)}</ul>
            </Notice>
          )}
          {refreshNote && (
            <Notice tone={refreshNote.changed ? "warn" : "info"}>
              <strong>{refreshNote.changed ? "The quote changed" : "The quote did not change"}</strong>
              <div>{refreshNote.summary}</div>
            </Notice>
          )}
          {data.items.map((item, index) => (
            <ItemCard key={item.itemId} item={item} index={index} items={data.items}
              onRefreshed={setRefreshNote} />
          ))}
          {data.rules.length > 0 && (
            <details>
              <summary>Rules being checked</summary>
              <ul>{data.rules.map((r) => <li key={r}>{r}</li>)}</ul>
            </details>
          )}
          <p className="muted"><small>Computed {dateTime(data.generatedAt)} on version {data.tripVersion}.</small></p>
        </>
      )}
    </div>
  );
}

function ItemCard({ item, index, items, onRefreshed }: {
  item: Item;
  index: number;
  items: Item[];
  onRefreshed: (r: Refreshed) => void;
}) {
  const { api, trip, allows, versioned } = useTrip();
  const titleOf = (id: string) => items.find((i) => i.itemId === id)?.title ?? id;

  function move(delta: number) {
    const ids = items.map((i) => i.itemId);
    const [moved] = ids.splice(index, 1);
    ids.splice(index + delta, 0, moved);
    void versioned(`move '${item.title}'`,
      (version) => api.put(`/trips/${trip.id}/itinerary/order`, { itemIds: ids, version }));
  }

  return (
    <article className="card item">
      <div className="row spread">
        <div>
          <span className="muted">{index + 1}.</span> <strong>{item.title}</strong>{" "}
          <Badge tone="neutral">{CATEGORY_LABEL[item.category] ?? item.category}</Badge>
        </div>
        <div className="price">
          {usd(item.quote.price)} <Badge tone="info">estimate</Badge>
        </div>
      </div>
      <div>
        {item.origin ? `${item.origin} → ` : ""}{item.destination} · {dateTime(item.startsAt)} – {dateTime(item.endsAt)}
      </div>
      <div className="row wrap">
        {item.quote.expired
          ? <Badge tone="warn">price expired since {dateTime(item.quote.validUntil)}</Badge>
          : <Badge tone="neutral">price valid until {dateTime(item.quote.validUntil)}</Badge>}
        {!item.quote.available && <Badge tone="bad">unavailable</Badge>}
        {item.booking
          ? <Badge tone={BOOKING[item.booking.status].tone}>{BOOKING[item.booking.status].label}</Badge>
          : <Badge tone="neutral">not booked</Badge>}
      </div>
      {item.flags.length > 0 && (
        <ul className="flags">
          {item.flags.map((f) => (
            <li key={f.code} className={FLAG_TONE[f.code] ?? "warn"}>
              <strong>{f.code === "SCHEDULE_CONFLICT" ? "Conflict" : "Warning"}:</strong> {f.message}
              {f.relatedItemIds.length > 0 && f.code !== "SCHEDULE_CONFLICT" &&
                ` (${f.relatedItemIds.map(titleOf).join(", ")})`}
            </li>
          ))}
        </ul>
      )}
      {allows("editor") && (
        <div className="row wrap actions">
          <button className="secondary" disabled={index === 0} aria-label={`move up ${item.title}`}
            onClick={() => move(-1)}>↑</button>
          <button className="secondary" disabled={index === items.length - 1} aria-label={`move down ${item.title}`}
            onClick={() => move(1)}>↓</button>
          <button className="secondary" onClick={async () => {
            const r = await versioned(`refresh the quote for '${item.title}'`,
              (version) => api.post<Refreshed>(`/trips/${trip.id}/itinerary/items/${item.itemId}/refresh`, { version }));
            if (r) onRefreshed(r);
          }}>Refresh quote</button>
          <button className="secondary" onClick={() => void versioned(`remove '${item.title}'`,
            (version) => api.del(`/trips/${trip.id}/itinerary/items/${item.itemId}?version=${version}`))}>
            Remove
          </button>
        </div>
      )}
    </article>
  );
}
