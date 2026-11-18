import { toApiError } from "../api";
import { Badge, ErrorBox } from "../components";
import { dateTime, DOCUMENT } from "../format";
import { useAction, useLoad, useTrip } from "../TripView";
import type { TripDocument } from "../types";

export function DocumentsTab() {
  const { api, trip, ticks, allows } = useTrip();
  const docs = useLoad(() => api.get<TripDocument[]>(`/trips/${trip.id}/documents`), [trip.id, ticks.document, ticks.trip]);
  const action = useAction();

  // The download needs the X-User-Id header, so it is fetched and saved as a blob.
  async function download(d: TripDocument) {
    await action.run(async () => {
      const response = await api.raw("GET", `/trips/${trip.id}/documents/${d.documentId}/download`);
      if (!response.ok) throw await toApiError(response);
      const url = URL.createObjectURL(await response.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `viaje-${trip.id}-v${d.tripVersion}.html`;
      a.click();
      URL.revokeObjectURL(url);
    });
    docs.reload();
  }

  return (
    <div>
      {allows("editor") && (
        <p>
          <button disabled={action.busy} onClick={async () => {
            await action.run(() => api.post(`/trips/${trip.id}/documents`));
            docs.reload();
          }}>Generar documento de la versión {trip.version}</button>
        </p>
      )}
      <ErrorBox error={docs.error} />
      <ErrorBox error={action.error} onClose={action.clear} />
      {docs.data?.length === 0 && <p className="muted">Todavía no hay documentos.</p>}
      <table>
        <thead><tr><th>Copia del viaje</th><th>Estado</th><th>Detalles</th><th /></tr></thead>
        <tbody>
          {(docs.data ?? []).map((d) => (
            <tr key={d.documentId} data-status={d.status}>
              <td>
                versión {d.tripVersion} · {dateTime(d.snapshotAt)}
                {d.tripVersion < trip.version && <div><Badge tone="warn">el viaje ya va por la versión {trip.version}</Badge></div>}
              </td>
              <td><Badge tone={DOCUMENT[d.status].tone}>{DOCUMENT[d.status].label}</Badge></td>
              <td className="muted">
                {d.error && <div className="bad-text">{d.error}</div>}
                {d.completedAt && <div>listo {dateTime(d.completedAt)}</div>}
                {d.sizeBytes != null && <div>{d.sizeBytes} bytes</div>}
                {d.sha256 && <div><small>sha256 {d.sha256.slice(0, 16)}…</small></div>}
                <div>pedido por {d.requestedBy}</div>
              </td>
              <td>
                {d.ready && <button className="secondary" onClick={() => void download(d)}>Descargar</button>}
                {d.status === "failed" && allows("editor") && (
                  <button className="secondary" onClick={async () => {
                    await action.run(() => api.post(`/trips/${trip.id}/documents/${d.documentId}/retry`));
                    docs.reload();
                  }}>Reintentar</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
