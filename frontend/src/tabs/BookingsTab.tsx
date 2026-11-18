import { useState } from "react";

import { ApiError } from "../api";
import { Badge, ErrorBox, Notice } from "../components";
import { BOOKING, CATEGORY_LABEL, dateTime, usd } from "../format";
import { useAction, useLoad, useTrip } from "../TripView";
import type { Booking, BookingStatus, Item, Itinerary, Terms } from "../types";

// An item can get a new booking only if it has none, or its last one ended without booking.
const CAN_START: (BookingStatus | undefined)[] = [undefined, "failed", "rejected"];

export function BookingsTab() {
  const { api, trip, ticks, allows } = useTrip();
  const bookings = useLoad(() => api.get<Booking[]>(`/trips/${trip.id}/bookings`), [trip.id, ticks.booking, ticks.trip]);
  const itinerary = useLoad(() => api.get<Itinerary>(`/trips/${trip.id}/itinerary`), [trip.id, ticks.booking, ticks.trip]);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const bookable = (itinerary.data?.items ?? []).filter((i) => CAN_START.includes(i.booking?.status));
  const reviewed = bookable.find((i) => i.itemId === reviewing);

  return (
    <div>
      <ErrorBox error={bookings.error ?? itinerary.error} />
      {allows("editor") && bookable.length > 0 && (
        <section>
          <h3>Not booked yet</h3>
          <ul className="plain">
            {bookable.map((i) => (
              <li key={i.itemId} className="row spread">
                <span>{i.title} · {usd(i.quote.price)} <Badge tone="info">estimate</Badge></span>
                <button className="secondary" onClick={() => setReviewing(i.itemId)}>Review terms</button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {reviewed && (
        <ReviewTerms key={reviewed.itemId + reviewed.quote.revision + reviewed.quote.price.amount}
          item={reviewed} onDone={() => { setReviewing(null); bookings.reload(); itinerary.reload(); }}
          onCancel={() => setReviewing(null)} />
      )}
      <h3>Bookings</h3>
      {bookings.data?.length === 0 && <p className="muted">No bookings yet.</p>}
      {(bookings.data ?? []).map((b) => (
        <BookingCard key={b.bookingId} booking={b} onChanged={bookings.reload} />
      ))}
    </div>
  );
}

/** The exact terms the user confirms: they are sent back so the backend books only them. */
function ReviewTerms({ item, onDone, onCancel }: { item: Item; onDone: () => void; onCancel: () => void }) {
  const { api, trip, versioned } = useTrip();
  const action = useAction();
  const q = item.quote;
  const error = action.error instanceof ApiError ? action.error : null;
  const current = (error?.details as { currentTerms?: Terms } | null)?.currentTerms;

  async function confirm() {
    const booking = await action.run(() => api.post<Booking>(
      `/trips/${trip.id}/itinerary/items/${item.itemId}/bookings`,
      { expectedRevision: q.revision, expectedPrice: q.price },
    ));
    if (booking) onDone();
  }

  async function refresh() {
    action.clear();
    await versioned(`refresh the quote for '${item.title}'`,
      (version) => api.post(`/trips/${trip.id}/itinerary/items/${item.itemId}/refresh`, { version }));
  }

  return (
    <section className="card review" aria-label="review terms">
      <h3>Review terms: {item.title}</h3>
      <dl>
        <dt>Category</dt><dd>{CATEGORY_LABEL[item.category] ?? item.category} · {item.providerId}</dd>
        <dt>Schedule</dt><dd>{dateTime(item.startsAt)} – {dateTime(item.endsAt)}</dd>
        <dt>Travelers</dt><dd>{item.travelers}</dd>
        <dt>Price</dt><dd><strong>{usd(q.price)}</strong> (quote {q.revision})</dd>
        <dt>Valid until</dt><dd>{dateTime(q.validUntil)} {q.expired && <Badge tone="warn">expired</Badge>}</dd>
        <dt>Available</dt><dd>{q.available ? "yes" : "no"}</dd>
        <dt>Restrictions</dt><dd>{item.restrictions.length ? item.restrictions.join("; ") : "none"}</dd>
      </dl>
      {q.expired && (
        <Notice tone="warn">The price expired: the provider will not accept these terms. Refresh the quote and review them again.</Notice>
      )}
      {error && (error.code === "TERMS_CHANGED" || error.code === "OFFER_UNAVAILABLE") ? (
        <Notice tone="bad">
          <strong>{error.status} {error.code}: nothing was booked</strong>
          <div>{error.message}</div>
          {current && (
            <div>
              Current terms: {usd(current.price)} · quote {current.revision} ·{" "}
              {current.available ? "available" : "unavailable"}
            </div>
          )}
          <button className="secondary" onClick={() => void refresh()}>Refresh quote and review again</button>
        </Notice>
      ) : (
        <ErrorBox error={action.error} onClose={action.clear} />
      )}
      <div className="row">
        <button onClick={() => void confirm()} disabled={action.busy}>
          {action.busy ? "Sending…" : `Confirm booking for ${usd(q.price)}`}
        </button>
        {q.expired && <button className="secondary" onClick={() => void refresh()}>Refresh quote</button>}
        <button className="secondary" onClick={onCancel}>Cancel</button>
      </div>
    </section>
  );
}

export function BookingCard({ booking: b, onChanged }: { booking: Booking; onChanged: () => void }) {
  const { api, trip, allows } = useTrip();
  const action = useAction();
  const status = BOOKING[b.status];
  return (
    <article className={`card booking ${b.booked ? "booked" : ""}`} data-status={b.status}>
      <div className="row spread">
        <strong>{b.itemTitle ?? b.offerId}</strong>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <p className="next-step"><strong>What to do:</strong> {b.nextStep}</p>
      <dl>
        <dt>Reviewed price</dt><dd>{usd(b.expectedPrice)} (quote {b.expectedRevision})</dd>
        {b.acceptedPrice && <><dt>Accepted price</dt><dd>{usd(b.acceptedPrice)}</dd></>}
        {b.providerBookingId && <><dt>Provider reference</dt><dd><code>{b.providerBookingId}</code></dd></>}
        <dt>Our reference</dt><dd><code>{b.clientReference}</code></dd>
        <dt>Requested by</dt><dd>{b.requestedBy} · {dateTime(b.createdAt)}</dd>
        <dt>Updated</dt><dd>{dateTime(b.updatedAt)}</dd>
      </dl>
      {b.lastError && (
        <div className="last-error">
          <strong>Last error:</strong> <code>{b.lastError.code}</code> {b.lastError.message}
          {b.lastError.retryAfterSeconds != null && ` · retry after ${b.lastError.retryAfterSeconds} s`}
          {b.lastError.at && ` · ${dateTime(b.lastError.at)}`}
          {b.lastError.providerRequestId && <small> · provider: {b.lastError.providerRequestId}</small>}
        </div>
      )}
      {b.failure && (
        <div className="last-error"><strong>Failure:</strong> <code>{b.failure.code}</code> {b.failure.message}</div>
      )}
      {b.currentTerms && (
        <div className="muted">Current provider terms: {usd(b.currentTerms.price)} · quote {b.currentTerms.revision}</div>
      )}
      <ErrorBox error={action.error} onClose={action.clear} />
      {b.canRetry && allows("editor") && (
        <button disabled={action.busy} onClick={async () => {
          if (await action.run(() => api.post(`/trips/${trip.id}/bookings/${b.bookingId}/retry`))) onChanged();
        }}>Retry (same request, no duplicate)</button>
      )}
    </article>
  );
}
