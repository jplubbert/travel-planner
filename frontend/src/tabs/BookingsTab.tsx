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
          <h3>Sin reservar</h3>
          <ul className="plain">
            {bookable.map((i) => (
              <li key={i.itemId} className="row spread">
                <span>{i.title} · {usd(i.quote.price)} <Badge tone="info">estimado</Badge></span>
                <button className="secondary" onClick={() => setReviewing(i.itemId)}>Revisar términos</button>
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
      <h3>Reservas</h3>
      {bookings.data?.length === 0 && <p className="muted">Todavía no hay reservas.</p>}
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
    await versioned(`actualizar la cotización de «${item.title}»`,
      (version) => api.post(`/trips/${trip.id}/itinerary/items/${item.itemId}/refresh`, { version }));
  }

  return (
    <section className="card review" aria-label="revisar términos">
      <h3>Revisar términos: {item.title}</h3>
      <dl>
        <dt>Categoría</dt><dd>{CATEGORY_LABEL[item.category] ?? item.category} · {item.providerId}</dd>
        <dt>Horario</dt><dd>{dateTime(item.startsAt)} – {dateTime(item.endsAt)}</dd>
        <dt>Viajeros</dt><dd>{item.travelers}</dd>
        <dt>Precio</dt><dd><strong>{usd(q.price)}</strong> (cotización {q.revision})</dd>
        <dt>Válido hasta</dt><dd>{dateTime(q.validUntil)} {q.expired && <Badge tone="warn">caducado</Badge>}</dd>
        <dt>Disponible</dt><dd>{q.available ? "sí" : "no"}</dd>
        <dt>Restricciones</dt><dd>{item.restrictions.length ? item.restrictions.join("; ") : "ninguna"}</dd>
      </dl>
      {q.expired && (
        <Notice tone="warn">El precio caducó: el proveedor no aceptará estos términos. Actualiza la cotización y revísalos de nuevo.</Notice>
      )}
      {error && (error.code === "TERMS_CHANGED" || error.code === "OFFER_UNAVAILABLE") ? (
        <Notice tone="bad">
          <strong>{error.status} {error.code}: no se reservó nada</strong>
          <div>{error.message}</div>
          {current && (
            <div>
              Términos actuales: {usd(current.price)} · cotización {current.revision} ·{" "}
              {current.available ? "disponible" : "no disponible"}
            </div>
          )}
          <button className="secondary" onClick={() => void refresh()}>Actualizar cotización y revisar de nuevo</button>
        </Notice>
      ) : (
        <ErrorBox error={action.error} onClose={action.clear} />
      )}
      <div className="row">
        <button onClick={() => void confirm()} disabled={action.busy}>
          {action.busy ? "Enviando…" : `Confirmar reserva por ${usd(q.price)}`}
        </button>
        {q.expired && <button className="secondary" onClick={() => void refresh()}>Actualizar cotización</button>}
        <button className="secondary" onClick={onCancel}>Cancelar</button>
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
      <p className="next-step"><strong>Qué hacer:</strong> {b.nextStep}</p>
      <dl>
        <dt>Precio revisado</dt><dd>{usd(b.expectedPrice)} (cotización {b.expectedRevision})</dd>
        {b.acceptedPrice && <><dt>Precio aceptado</dt><dd>{usd(b.acceptedPrice)}</dd></>}
        {b.providerBookingId && <><dt>Referencia del proveedor</dt><dd><code>{b.providerBookingId}</code></dd></>}
        <dt>Referencia propia</dt><dd><code>{b.clientReference}</code></dd>
        <dt>Pedida por</dt><dd>{b.requestedBy} · {dateTime(b.createdAt)}</dd>
        <dt>Actualizada</dt><dd>{dateTime(b.updatedAt)}</dd>
      </dl>
      {b.lastError && (
        <div className="last-error">
          <strong>Último error:</strong> <code>{b.lastError.code}</code> {b.lastError.message}
          {b.lastError.retryAfterSeconds != null && ` · reintentar tras ${b.lastError.retryAfterSeconds} s`}
          {b.lastError.at && ` · ${dateTime(b.lastError.at)}`}
          {b.lastError.providerRequestId && <small> · proveedor: {b.lastError.providerRequestId}</small>}
        </div>
      )}
      {b.failure && (
        <div className="last-error"><strong>Fallo:</strong> <code>{b.failure.code}</code> {b.failure.message}</div>
      )}
      {b.currentTerms && (
        <div className="muted">Términos actuales del proveedor: {usd(b.currentTerms.price)} · cotización {b.currentTerms.revision}</div>
      )}
      <ErrorBox error={action.error} onClose={action.clear} />
      {b.canRetry && allows("editor") && (
        <button disabled={action.busy} onClick={async () => {
          if (await action.run(() => api.post(`/trips/${trip.id}/bookings/${b.bookingId}/retry`))) onChanged();
        }}>Reintentar (misma petición, sin duplicar)</button>
      )}
    </article>
  );
}
