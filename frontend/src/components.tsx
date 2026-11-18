import type { ReactNode } from "react";

import { ApiError } from "./api";
import { dateTime, errorMessage, type Tone } from "./format";
import type { VersionConflictDetails } from "./types";

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Notice({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <div className={`notice ${tone}`} role={tone === "bad" ? "alert" : "status"}>{children}</div>;
}

/** Shows a backend error: status, code, details and requestId as returned; the message in
 *  Spanish for known codes, with the backend's original one kept alongside (ADR 0008). */
export function ErrorBox({ error, onClose }: { error: unknown; onClose?: () => void }) {
  if (!error) return null;
  const e = error instanceof ApiError ? error : null;
  const fields = e && isFields(e.details) ? e.details.fields : [];
  return (
    <Notice tone="bad">
      <div className="row spread">
        <strong>
          {e ? `${e.status} ${e.code}` : "Error de red"}
        </strong>
        {onClose && <button className="link" onClick={onClose}>cerrar</button>}
      </div>
      <div>{e ? errorMessage(e.code, e.message) : String(error)}</div>
      {e && errorMessage(e.code, e.message) !== e.message && (
        <details><summary>Mensaje original del backend</summary><small>{e.message}</small></details>
      )}
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
      <strong>409 TRIP_VERSION_CONFLICT: alguien cambió el viaje antes que tú</strong>
      <p>
        Tu cambio ({conflict.label}) se basaba en la versión {details.yourVersion}; la actual es la{" "}
        {details.currentVersion}. No se aplicó nada. Cambios desde tu versión:
      </p>
      <ul className="changes">
        {details.changesSinceYourVersion.map((c) => (
          <li key={c.version}>
            <strong>v{c.version}</strong> · {c.by} · {dateTime(c.at)} · {c.summary}
          </li>
        ))}
      </ul>
      <p>La vista ya muestra el estado actual. Revisa si tu cambio sigue teniendo sentido.</p>
      <div className="row">
        <button onClick={onReapply} disabled={busy}>
          Reaplicar «{conflict.label}» sobre la versión {details.currentVersion}
        </button>
        <button className="secondary" onClick={onDiscard} disabled={busy}>Descartar mi cambio</button>
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
