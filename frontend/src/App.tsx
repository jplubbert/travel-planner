import { useEffect, useMemo, useState } from "react";

import { createApi } from "./api";
import { Badge, ErrorBox, Notice } from "./components";
import { day, ROLE_LABEL, usd } from "./format";
import { TripFields } from "./tabs/ParticipantsTab";
import { TripView, useAction, useLoad } from "./TripView";
import type { Trip, TripInput } from "./types";

// Test identities seeded by the backend migrations. Identity is a header, not a login.
export const TEST_USERS = ["ana", "beto", "carla", "dani"] as const;

function stored(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function store(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // storage unavailable: the choice just is not remembered
  }
}

const tripFromHash = () => /^#\/trips\/([\w-]+)/.exec(window.location.hash)?.[1] ?? null;

export function App() {
  const [userId, setUserId] = useState(() => stored("tp-user", "ana"));
  const [showAll, setShowAll] = useState(false);
  const [tripId, setTripId] = useState<string | null>(tripFromHash);
  const api = useMemo(() => createApi(userId), [userId]);

  useEffect(() => {
    const onHash = () => setTripId(tripFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const open = (id: string | null) => {
    window.location.hash = id ? `#/trips/${id}` : "";
    setTripId(id);
  };

  return (
    <div className="app">
      <header className="top">
        <h1><a href="#" onClick={(e) => { e.preventDefault(); open(null); }}>Travel Planner</a></h1>
        <div className="row wrap">
          <label className="row">
            <span>Usuario de prueba</span>
            <select aria-label="usuario de prueba" value={userId}
              onChange={(e) => { setUserId(e.target.value); store("tp-user", e.target.value); }}>
              {TEST_USERS.map((u) => <option key={u} value={u}>{u}</option>)}
            </select>
          </label>
          <label className="row small">
            <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
            <span>Mostrar acciones aunque mi rol no las permita (el backend responde 403)</span>
          </label>
        </div>
      </header>
      <main>
        {tripId
          ? <TripView key={`${userId}:${tripId}`} api={api} tripId={tripId} showAll={showAll} onBack={() => open(null)} />
          : <TripList key={userId} api={api} onOpen={open} />}
      </main>
    </div>
  );
}

function TripList({ api, onOpen }: { api: ReturnType<typeof createApi>; onOpen: (id: string) => void }) {
  const trips = useLoad(() => api.get<Trip[]>("/trips"), [api]);
  const [creating, setCreating] = useState(false);
  return (
    <section>
      <div className="row spread">
        <h2>Mis viajes</h2>
        {!creating && <button onClick={() => setCreating(true)}>Nuevo viaje</button>}
      </div>
      {creating && <CreateTrip api={api} onCreated={(t) => onOpen(t.id)} onCancel={() => setCreating(false)} />}
      <ErrorBox error={trips.error} />
      {trips.data?.length === 0 && <Notice tone="neutral">No participas en ningún viaje todavía.</Notice>}
      <ul className="plain trips">
        {(trips.data ?? []).map((t) => (
          <li key={t.id}>
            <a href={`#/trips/${t.id}`} onClick={(e) => { e.preventDefault(); onOpen(t.id); }}>
              <strong>{t.name}</strong>
            </a>{" "}
            <Badge tone="neutral">{t.myRole ? ROLE_LABEL[t.myRole] : "—"}</Badge>
            <div className="muted">
              {t.destinations.join(" → ")} · {day(t.startsOn)} – {day(t.endsOn)} · {t.travelers} viajeros · {usd(t.budget)}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function CreateTrip({ api, onCreated, onCancel }: {
  api: ReturnType<typeof createApi>;
  onCreated: (t: Trip) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<TripInput>({
    name: "", destinations: [], startsOn: "", endsOn: "", travelers: 2, budget: "",
  });
  const action = useAction();
  return (
    <form className="card" onSubmit={async (e) => {
      e.preventDefault();
      const trip = await action.run(() => api.post<Trip>("/trips", form));
      if (trip) onCreated(trip);
    }}>
      <h3>Nuevo viaje</h3>
      <TripFields value={form} onChange={setForm} />
      <ErrorBox error={action.error} onClose={action.clear} />
      <div className="row">
        <button type="submit" disabled={action.busy}>Crear</button>
        <button type="button" className="secondary" onClick={onCancel}>Cancelar</button>
      </div>
    </form>
  );
}
