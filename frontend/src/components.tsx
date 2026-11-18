import type { ReactNode } from "react";

import { ApiError } from "./api";
import { dateTime, type Tone } from "./format";
import type { VersionConflictDetails } from "./types";

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Notice({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <div className={`notice ${tone}`} role={tone === "bad" ? "alert" : "status"}>{children}</div>;
}

/** Shows a backend error as returned: status, code, message, details and requestId. */
export function ErrorBox({ error, onClose }: { error: unknown; onClose?: () => void }) {
  if (!error) return null;
  const e = error instanceof ApiError ? error : null;
  const fields = e && isFields(e.details) ? e.details.fields : [];
  return (
    <Notice tone="bad">
      <div className="row spread">
        <strong>
          {e ? `${e.status} ${e.code}` : "Network error"}
        </strong>
        {onClose && <button className="link" onClick={onClose}>close</button>}
      </div>
      <div>{e ? e.message : String(error)}</div>
      {fields.length > 0 && (
        <ul>{fields.map((f, i) => <li key={i}><code>{f.field}</code>: {f.message}</li>)}</ul>
      )}
      {e && !fields.length && e.details != null && e.code !== "TRIP_VERSION_CONFLICT" && (
        <pre className="details">{JSON.stringify(e.details, null, 2)}</pre>
      )}
      {e?.requestId && <small>requestId: {e.requestId}</small>}
    </Notice>
  );
}

function isFields(d: unknown): d is { fields: { field: string; message: string }[] } {
  return typeof d === "object" && d !== null && Array.isArray((d as { fields?: unknown }).fields);
}

export type PendingConflict = {
  label: string;
  details: VersionConflictDetails;
  reapply: (version: number) => Promise<unknown>;
};

/** 409 TRIP_VERSION_CONFLICT: what changed since the version the user had, and the option
 *  to apply the same change again on top of the current version. */
export function ConflictPanel({ conflict, onReapply, onDiscard, busy }: {
  conflict: PendingConflict;
  onReapply: () => void;
  onDiscard: () => void;
  busy?: boolean;
}) {
  const { details } = conflict;
  return (
    <Notice tone="warn">
      <strong>409 TRIP_VERSION_CONFLICT: someone changed the trip before you</strong>
      <p>
        Your change ({conflict.label}) was based on version {details.yourVersion}; the current one is{" "}
        {details.currentVersion}. Nothing was applied. Changes since your version:
      </p>
      <ul className="changes">
        {details.changesSinceYourVersion.map((c) => (
          <li key={c.version}>
            <strong>v{c.version}</strong> · {c.by} · {dateTime(c.at)} · {c.summary}
          </li>
        ))}
      </ul>
      <p>The view already shows the current state. Check whether your change still makes sense.</p>
      <div className="row">
        <button onClick={onReapply} disabled={busy}>
          Reapply “{conflict.label}” on version {details.currentVersion}
        </button>
        <button className="secondary" onClick={onDiscard} disabled={busy}>Discard my change</button>
      </div>
    </Notice>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}
