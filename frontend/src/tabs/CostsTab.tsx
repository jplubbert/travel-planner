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
      {!c ? <p>Loading costs…</p> : (
        <>
          <div className="totals">
            <div><span className="muted">Estimated (unconfirmed)</span><strong>{usd(c.estimatedTotal)}</strong></div>
            <div><span className="muted">Confirmed by the provider</span><strong>{usd(c.confirmedTotal)}</strong></div>
            <div><span className="muted">Total</span><strong>{usd(c.total)}</strong></div>
            <div><span className="muted">Budget</span><strong>{usd(c.budget.amount)}</strong></div>
            <div>
              <span className="muted">{c.budget.overBudget ? "Over budget" : "Remaining"}</span>
              <strong className={c.budget.overBudget ? "bad-text" : ""}>{usd(c.budget.remaining)}</strong>
            </div>
          </div>
          {c.budget.overBudget && (
            <Notice tone="bad">The total exceeds the budget ({c.budget.usedPercent ?? "—"} % used).</Notice>
          )}
          {!c.budget.overBudget && c.budget.usedPercent != null && (
            <p className="muted">{c.budget.usedPercent} % of the budget used.</p>
          )}
          <table>
            <thead><tr><th>Item</th><th>Amount</th><th>Basis</th><th>Note</th></tr></thead>
            <tbody>
              {c.lines.map((l) => (
                <tr key={l.itemId}>
                  <td>{l.title} <span className="muted">· {CATEGORY_LABEL[l.category] ?? l.category}</span></td>
                  <td>{usd(l.amount)}</td>
                  <td>
                    {l.basis === "confirmed"
                      ? <Badge tone="ok">confirmed</Badge>
                      : <Badge tone="info">estimate</Badge>}
                    {l.bookingStatus && l.basis !== "confirmed" && (
                      <> <Badge tone={BOOKING[l.bookingStatus].tone}>{BOOKING[l.bookingStatus].label}</Badge></>
                    )}
                  </td>
                  <td className="muted">{l.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted"><small>{c.rounding} · version {c.tripVersion} · amounts in USD.</small></p>
        </>
      )}
    </div>
  );
}
