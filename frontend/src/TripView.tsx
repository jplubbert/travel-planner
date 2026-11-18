import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { ApiError, type Api, API_BASE } from "./api";
import { Badge, ConflictPanel, ErrorBox, Notice, type PendingConflict } from "./components";
import { day, ROLE_LABEL, usd } from "./format";
import { followEvents, type StreamStatus } from "./sse";
import { AssistantTab } from "./tabs/AssistantTab";
import { BookingsTab } from "./tabs/BookingsTab";
import { CostsTab } from "./tabs/CostsTab";
import { DocumentsTab } from "./tabs/DocumentsTab";
import { ItineraryTab } from "./tabs/ItineraryTab";
import { ParticipantsTab } from "./tabs/ParticipantsTab";
import { SearchTab } from "./tabs/SearchTab";
import type { Role, Trip, VersionConflictDetails } from "./types";

const RANK: Record<Role, number> = { viewer: 0, editor: 1, organizer: 2 };

export type Ticks = { trip: number; booking: number; document: number };

export type TripCtx = {
  api: Api;
  trip: Trip;
  /** Whether to show an action needing ``min``. Only a UI hint: the backend decides. */
  allows: (min: Role) => boolean;
  ticks: Ticks;
  /** Runs a mutation that needs the trip version (ADR 0001). A 409 version conflict opens
   *  the conflict panel; any other error is shown as returned. Both return undefined. */
  versioned: <T>(label: string, op: (version: number) => Promise<T>) => Promise<T | undefined>;
  reloadTrip: () => Promise<void>;
};

const Ctx = createContext<TripCtx | null>(null);

export function useTrip(): TripCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTrip outside TripView");
  return ctx;
}

const TABS = [
  ["participants", "Participantes"],
  ["search", "Búsqueda"],
  ["itinerary", "Itinerario"],
  ["costs", "Costes"],
  ["bookings", "Reservas"],
  ["documents", "Documentos"],
  ["assistant", "Asistente"],
] as const;
type Tab = (typeof TABS)[number][0];

const STREAM_LABEL: Record<StreamStatus, string> = {
  connecting: "conectando…",
  live: "en vivo",
  reconnecting: "reconectando…",
  closed: "desconectado",
};

export function TripView({ api, tripId, showAll, onBack }: {
  api: Api;
  tripId: string;
  showAll: boolean;
  onBack: () => void;
}) {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [error, setError] = useState<unknown>(null);
  const [conflict, setConflict] = useState<PendingConflict | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>("itinerary");
  const [ticks, setTicks] = useState<Ticks>({ trip: 0, booking: 0, document: 0 });
  const [stream, setStream] = useState<StreamStatus>("connecting");
  const [revoked, setRevoked] = useState(false);

  const reloadTrip = useCallback(async () => {
    try {
      setTrip(await api.get<Trip>(`/trips/${tripId}`));
      setLoadError(null);
    } catch (e) {
      setLoadError(e);
    }
  }, [api, tripId]);

  useEffect(() => {
    setTrip(null);
    setConflict(null);
    setError(null);
    setRevoked(false);
    void reloadTrip();
  }, [reloadTrip]);

  // Live status: hints only. "ready" arrives on every (re)connect: re-read everything.
  useEffect(() => {
    const abort = new AbortController();
    const bump = (...kinds: (keyof Ticks)[]) =>
      setTicks((t) => {
        const next = { ...t };
        for (const k of kinds) next[k] += 1;
        return next;
      });
    void followEvents(
      `${API_BASE}/trips/${tripId}/events`,
      { "X-User-Id": api.userId },
      (e) => {
        if (e.event === "ready") {
          void reloadTrip();
          bump("trip", "booking", "document");
        } else if (e.event === "trip") {
          void reloadTrip();
          bump("trip");
        } else if (e.event === "booking") bump("booking");
        else if (e.event === "document") bump("document");
        else if (e.event === "access-revoked") setRevoked(true);
      },
      setStream,
      abort.signal,
    );
    return () => abort.abort();
  }, [api, tripId, reloadTrip]);

  const versioned = useCallback(
    async <T,>(label: string, op: (version: number) => Promise<T>): Promise<T | undefined> => {
      if (!trip) return undefined;
      setError(null);
      try {
        const result = await op(trip.version);
        setConflict(null);
        await reloadTrip();
        setTicks((t) => ({ ...t, trip: t.trip + 1 }));
        return result;
      } catch (e) {
        if (e instanceof ApiError && e.code === "TRIP_VERSION_CONFLICT") {
          const details = e.details as VersionConflictDetails;
          setConflict({ label, details, reapply: op });
          setTrip((t) => (t ? { ...details.current, myRole: t.myRole } : t));
          setTicks((t) => ({ ...t, trip: t.trip + 1 }));
          return undefined;
        }
        setError(e);
        return undefined;
      }
    },
    [trip, reloadTrip],
  );

  async function reapply() {
    if (!conflict) return;
    const { reapply: op, details, label } = conflict;
    setBusy(true);
    setError(null);
    try {
      await op(details.currentVersion);
      setConflict(null);
      await reloadTrip();
      setTicks((t) => ({ ...t, trip: t.trip + 1 }));
    } catch (e) {
      if (e instanceof ApiError && e.code === "TRIP_VERSION_CONFLICT") {
        // Changed again meanwhile: show the newer conflict.
        setConflict({ label, details: e.details as VersionConflictDetails, reapply: op });
        setTicks((t) => ({ ...t, trip: t.trip + 1 }));
      } else {
        setConflict(null);
        setError(e);
      }
    } finally {
      setBusy(false);
    }
  }

  if (loadError && !trip) {
    return (
      <section>
        <button className="link" onClick={onBack}>← viajes</button>
        <ErrorBox error={loadError} />
      </section>
    );
  }
  if (!trip) return <p>Cargando viaje…</p>;

  const role = trip.myRole;
  const allows = (min: Role) => showAll || (role !== null && RANK[role] >= RANK[min]);
  const ctx: TripCtx = { api, trip, allows, ticks, versioned, reloadTrip };

  return (
    <Ctx.Provider value={ctx}>
      <section>
        <button className="link" onClick={onBack}>← viajes</button>
        <header className="trip-header">
          <div>
            <h2>{trip.name}</h2>
            <div className="muted">
              {trip.destinations.join(" → ")} · {day(trip.startsOn)} – {day(trip.endsOn)} ·{" "}
              {trip.travelers} viajeros · presupuesto {usd(trip.budget)}
            </div>
          </div>
          <div className="right">
            <Badge tone="neutral">tu rol: {role ? ROLE_LABEL[role] : "—"}</Badge>{" "}
            <Badge tone="neutral">versión {trip.version}</Badge>{" "}
            <Badge tone={stream === "live" ? "ok" : "warn"}>estado: {STREAM_LABEL[stream]}</Badge>
          </div>
        </header>
        {revoked && (
          <Notice tone="bad">Has perdido el acceso a este viaje. Lo que ves ya no se actualiza.</Notice>
        )}
        {conflict && (
          <ConflictPanel conflict={conflict} busy={busy} onReapply={() => void reapply()}
            onDiscard={() => setConflict(null)} />
        )}
        <ErrorBox error={error} onClose={() => setError(null)} />
        <nav className="tabs" role="tablist">
          {TABS.map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id}
              className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </nav>
        <div className="tab-body">
          {tab === "participants" && <ParticipantsTab />}
          {tab === "search" && <SearchTab />}
          {tab === "itinerary" && <ItineraryTab />}
          {tab === "costs" && <CostsTab />}
          {tab === "bookings" && <BookingsTab />}
          {tab === "documents" && <DocumentsTab />}
          {tab === "assistant" && <AssistantTab />}
        </div>
      </section>
    </Ctx.Provider>
  );
}

/** Loads data for a tab and reloads it whenever one of ``deps`` changes. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let current = true;
    load().then(
      (d) => current && (setData(d), setError(null)),
      (e) => current && setError(e),
    );
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  return { data, setData, error, reload: () => setN((x) => x + 1) };
}

/** Runs a non-versioned action and keeps its error (shown as returned by the backend). */
export function useAction() {
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function run<T>(op: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError(null);
    try {
      return await op();
    } catch (e) {
      setError(e);
      return undefined;
    } finally {
      setBusy(false);
    }
  }
  return { run, error, busy, clear: () => setError(null) };
}
