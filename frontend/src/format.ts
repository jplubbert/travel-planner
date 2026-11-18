import type { BookingStatus, DocumentStatus, Money, Role, SearchOutcome } from "./types";

export const usd = (m: Money | null | undefined): string => (m ? `${m.amount} USD` : "—");

export const dateTime = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "—";

export const day = (iso: string): string =>
  new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString("en-US", { dateStyle: "medium" });

/** "12 s ago" style age of a timestamp relative to now. */
export function age(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "—";
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min ago` : `${Math.round(minutes / 60)} h ago`;
}

export type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

/** Roles are shown with their API names (ADR 0008). */
export const ROLE_LABEL: Record<Role, string> = {
  organizer: "organizer",
  editor: "editor",
  viewer: "viewer",
};

/** Roles a participant can be given (the organizer role is not assignable). */
export const ASSIGNABLE_ROLES = ["editor", "viewer"] as const satisfies readonly Role[];

/** Only "confirmed" is presented as booked; every other state says it is not (yet). */
export const BOOKING: Record<BookingStatus, { label: string; tone: Tone }> = {
  confirmed: { label: "Booked: confirmed by the provider", tone: "ok" },
  pending: { label: "Accepted, awaiting confirmation (not booked yet)", tone: "info" },
  submitted: { label: "Sending to the provider (not booked yet)", tone: "info" },
  unknown: { label: "Outcome unknown: it may or may not be booked; do not book again", tone: "warn" },
  draft: { label: "Not booked: the provider did not accept the request", tone: "bad" },
  rejected: { label: "Not booked: the provider rejected these terms", tone: "bad" },
  failed: { label: "Not booked: accepted, then failed", tone: "bad" },
};

export const DOCUMENT: Record<DocumentStatus, { label: string; tone: Tone }> = {
  waiting: { label: "Queued", tone: "info" },
  in_progress: { label: "Generating", tone: "info" },
  complete: { label: "Ready", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
};

export const SEARCH: Record<SearchOutcome, { label: string; tone: Tone }> = {
  complete: { label: "Search complete: every provider answered", tone: "ok" },
  partial: { label: "Partial results: some providers did not answer", tone: "warn" },
  empty: { label: "No options: every provider answered and nothing matches", tone: "neutral" },
  failed: { label: "Search failed: this is not the same as no options", tone: "bad" },
};

export const CATEGORY_LABEL: Record<string, string> = {
  transport: "Transport",
  accommodation: "Accommodation",
  activity: "Activity",
};

export const canEdit = (role: Role | null | undefined) => role === "organizer" || role === "editor";
