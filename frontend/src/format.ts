import type { BookingStatus, DocumentStatus, Money, Role, SearchOutcome } from "./types";

export const usd = (m: Money | null | undefined): string => (m ? `${m.amount} USD` : "—");

export const dateTime = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleString("es-ES", { dateStyle: "medium", timeStyle: "short" }) : "—";

export const day = (iso: string): string =>
  new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString("es-ES", { dateStyle: "medium" });

/** "hace 12 s" style age of a timestamp relative to now. */
export function age(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "—";
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `hace ${seconds} s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `hace ${minutes} min` : `hace ${Math.round(minutes / 60)} h`;
}

export type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

export const ROLE_LABEL: Record<Role, string> = {
  organizer: "organizador",
  editor: "editor",
  viewer: "viewer",
};

/** Only "confirmed" is presented as booked; every other state says it is not (yet). */
export const BOOKING: Record<BookingStatus, { label: string; tone: Tone }> = {
  confirmed: { label: "Reservado: confirmado por el proveedor", tone: "ok" },
  pending: { label: "Aceptada, pendiente de confirmación (aún no reservada)", tone: "info" },
  submitted: { label: "Enviando al proveedor (aún no reservada)", tone: "info" },
  unknown: { label: "Resultado desconocido: puede o no estar reservada; no reservar otra vez", tone: "warn" },
  draft: { label: "No reservada: el proveedor no aceptó la petición", tone: "bad" },
  rejected: { label: "No reservada: el proveedor rechazó estos términos", tone: "bad" },
  failed: { label: "No reservada: aceptada y luego fallida", tone: "bad" },
};

export const DOCUMENT: Record<DocumentStatus, { label: string; tone: Tone }> = {
  waiting: { label: "En cola", tone: "info" },
  in_progress: { label: "Generándose", tone: "info" },
  complete: { label: "Listo", tone: "ok" },
  failed: { label: "Falló", tone: "bad" },
};

export const SEARCH: Record<SearchOutcome, { label: string; tone: Tone }> = {
  complete: { label: "Búsqueda completa: respondieron todos los proveedores", tone: "ok" },
  partial: { label: "Resultados parciales: algunos proveedores no respondieron", tone: "warn" },
  empty: { label: "Sin opciones: todos respondieron y nada encaja", tone: "neutral" },
  failed: { label: "La búsqueda falló: no es lo mismo que no haber opciones", tone: "bad" },
};

export const CATEGORY_LABEL: Record<string, string> = {
  transport: "Transporte",
  accommodation: "Alojamiento",
  activity: "Actividad",
};

export const canEdit = (role: Role | null | undefined) => role === "organizer" || role === "editor";
