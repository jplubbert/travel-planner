import { Badge, ErrorBox, Notice } from "../components";
import { BOOKING, CATEGORY_LABEL, usd } from "../format";
import { useLoad, useTrip } from "../TripView";
import type { Costs } from "../types";

export function CostsTab() {
  const { api, trip, ticks } = useTrip();
  const costs = useLoad(() => api.get<Costs>(`/trips/${trip.id}/costs`), [trip.id, ticks.trip, ticks.booking]);
  const c = costs.data;
  return (
    <div>
      <ErrorBox error={costs.error} />
      {!c ? <p>Cargando costes…</p> : (
        <>
          <div className="totals">
            <div><span className="muted">Estimado (sin confirmar)</span><strong>{usd(c.estimatedTotal)}</strong></div>
            <div><span className="muted">Confirmado por el proveedor</span><strong>{usd(c.confirmedTotal)}</strong></div>
            <div><span className="muted">Total</span><strong>{usd(c.total)}</strong></div>
            <div><span className="muted">Presupuesto</span><strong>{usd(c.budget.amount)}</strong></div>
            <div>
              <span className="muted">{c.budget.overBudget ? "Por encima del presupuesto" : "Restante"}</span>
              <strong className={c.budget.overBudget ? "bad-text" : ""}>{usd(c.budget.remaining)}</strong>
            </div>
          </div>
          {c.budget.overBudget && (
            <Notice tone="bad">El total supera el presupuesto ({c.budget.usedPercent ?? "—"} % usado).</Notice>
          )}
          {!c.budget.overBudget && c.budget.usedPercent != null && (
            <p className="muted">{c.budget.usedPercent} % del presupuesto usado.</p>
          )}
          <table>
            <thead><tr><th>Elemento</th><th>Importe</th><th>Base</th><th>Nota</th></tr></thead>
            <tbody>
              {c.lines.map((l) => (
                <tr key={l.itemId}>
                  <td>{l.title} <span className="muted">· {CATEGORY_LABEL[l.category] ?? l.category}</span></td>
                  <td>{usd(l.amount)}</td>
                  <td>
                    {l.basis === "confirmed"
                      ? <Badge tone="ok">confirmado</Badge>
                      : <Badge tone="info">estimado</Badge>}
                    {l.bookingStatus && l.basis !== "confirmed" && (
                      <> <Badge tone={BOOKING[l.bookingStatus].tone}>{BOOKING[l.bookingStatus].label}</Badge></>
                    )}
                  </td>
                  <td className="muted">{l.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted"><small>{c.rounding} · versión {c.tripVersion} · importes en USD.</small></p>
        </>
      )}
    </div>
  );
}
