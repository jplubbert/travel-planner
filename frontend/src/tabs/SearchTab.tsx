import { useEffect, useState } from "react";

import { Badge, ErrorBox, Field, Notice } from "../components";
import { age, CATEGORY_LABEL, dateTime, SEARCH, usd } from "../format";
import { useAction, useTrip } from "../TripView";
import type { Offer, SearchInput, SearchResult } from "../types";

export function SearchTab() {
  const { api, trip } = useTrip();
  const [query, setQuery] = useState<SearchInput>({
    category: "accommodation", destination: trip.destinations[0] ?? "", origin: "",
    startsOn: trip.startsOn, endsOn: trip.endsOn,
  });
  const [result, setResult] = useState<SearchResult | null>(null);
  const action = useAction();

  async function search() {
    const body: SearchInput = { category: query.category, destination: query.destination };
    if (query.category === "transport" && query.origin) body.origin = query.origin;
    if (query.startsOn) body.startsOn = query.startsOn;
    if (query.endsOn) body.endsOn = query.endsOn;
    const found = await action.run(() => api.search<SearchResult>(`/trips/${trip.id}/offers/search`, body));
    if (found) setResult(found);
  }

  return (
    <div>
      <form className="row wrap" onSubmit={(e) => { e.preventDefault(); void search(); }}>
        <Field label="Categoría">
          <select value={query.category} onChange={(e) => setQuery({ ...query, category: e.target.value })}>
            <option value="accommodation">Alojamiento</option>
            <option value="transport">Transporte</option>
            <option value="activity">Actividad</option>
          </select>
        </Field>
        <Field label="Destino">
          <input list="trip-destinations" value={query.destination}
            onChange={(e) => setQuery({ ...query, destination: e.target.value })} />
        </Field>
        <datalist id="trip-destinations">
          {trip.destinations.map((d) => <option key={d} value={d} />)}
        </datalist>
        {query.category === "transport" && (
          <Field label="Origen">
            <input value={query.origin ?? ""} onChange={(e) => setQuery({ ...query, origin: e.target.value })} />
          </Field>
        )}
        <Field label="Desde">
          <input type="date" value={query.startsOn ?? ""} onChange={(e) => setQuery({ ...query, startsOn: e.target.value })} />
        </Field>
        <Field label="Hasta">
          <input type="date" value={query.endsOn ?? ""} onChange={(e) => setQuery({ ...query, endsOn: e.target.value })} />
        </Field>
        <button type="submit" disabled={action.busy}>{action.busy ? "Buscando…" : "Buscar"}</button>
      </form>
      <ErrorBox error={action.error} onClose={action.clear} />
      {result && <SearchResults result={result} />}
    </div>
  );
}

/** One rendering per outcome: complete, partial, empty and failed look different. */
export function SearchResults({ result }: { result: SearchResult }) {
  const now = useNow(5000);
  const outcome = SEARCH[result.outcome];
  return (
    <div className="search-results" data-outcome={result.outcome}>
      <Notice tone={outcome.tone}>
        <strong>{outcome.label}</strong>
        <div>{result.message}</div>
        {result.failedSuppliers.length > 0 && (
          <ul>
            {result.failedSuppliers.map((f) => (
              <li key={f.providerId}>Sin respuesta de <code>{f.providerId}</code>: {f.code} — {f.message}</li>
            ))}
          </ul>
        )}
        {result.error && (
          <div>
            Error: <code>{result.error.code}</code> {result.error.message}
            {result.error.retryAfterSeconds != null && ` · reintentar en ${result.error.retryAfterSeconds} s`}
            {result.error.providerRequestId && <small> · proveedor: {result.error.providerRequestId}</small>}
          </div>
        )}
      </Notice>
      <p className="muted freshness">
        Consultado {dateTime(result.searchedAt ?? result.receivedAt)} · recibido {age(result.receivedAt, now)}
        {" "}({result.query.travelers} viajeros, {result.query.startsOn} – {result.query.endsOn})
      </p>
      {result.outcome !== "failed" && <p className="muted"><small>{result.disclaimer}</small></p>}
      {result.offers.map((o) => <OfferCard key={o.offerId} offer={o} now={now} />)}
    </div>
  );
}

function OfferCard({ offer, now }: { offer: Offer; now: number }) {
  const { api, trip, allows, versioned } = useTrip();
  const [added, setAdded] = useState(false);
  const expired = offer.quoteExpired || new Date(offer.validUntil).getTime() <= now;
  return (
    <article className="card offer">
      <div className="row spread">
        <div>
          <strong>{offer.title}</strong>{" "}
          <Badge tone="neutral">{CATEGORY_LABEL[offer.category] ?? offer.category}</Badge>{" "}
          <span className="muted">{offer.providerId}</span>
        </div>
        <div className="price">
          {usd(offer.price)} <Badge tone="info">precio estimado</Badge>
        </div>
      </div>
      <div className="muted">{offer.description}</div>
      <div>
        {offer.origin ? `${offer.origin} → ` : ""}{offer.destination} · {dateTime(offer.startsAt)} – {dateTime(offer.endsAt)}
        {" "}· {offer.travelers} viajeros
      </div>
      <div className="row wrap">
        {offer.available ? <Badge tone="ok">disponible</Badge> : <Badge tone="bad">no disponible</Badge>}
        {expired
          ? <Badge tone="warn">cotización caducada ({dateTime(offer.validUntil)})</Badge>
          : <Badge tone="neutral">válida hasta {dateTime(offer.validUntil)}</Badge>}
      </div>
      {offer.restrictions.length > 0 && (
        <ul className="restrictions">{offer.restrictions.map((r) => <li key={r}>{r}</li>)}</ul>
      )}
      {allows("editor") && (
        <button disabled={added || !offer.available} onClick={async () => {
          const done = await versioned(`añadir «${offer.title}»`,
            (version) => api.post(`/trips/${trip.id}/itinerary/items`, { offerId: offer.offerId, version }));
          if (done) setAdded(true);
        }}>{added ? "Añadido al itinerario" : "Añadir al itinerario"}</button>
      )}
    </article>
  );
}

export function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
