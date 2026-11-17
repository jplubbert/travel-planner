"""Renders a document snapshot as a self-contained HTML file (inline CSS, no scripts, no
external resources). Deterministic: the output depends only on the snapshot."""

from html import escape
from typing import Any

CSS = """
body{font-family:system-ui,sans-serif;max-width:56rem;margin:2rem auto;padding:0 1rem;
color:#1b1f24;line-height:1.45}
h1{margin-bottom:.25rem}h2{margin-top:2rem;border-bottom:1px solid #d0d7de}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.35rem .5rem;
border-bottom:1px solid #eaeef2;vertical-align:top}td.num,th.num{text-align:right}
.meta{color:#57606a}.flag{color:#9a6700}.tag{font-size:.8rem;padding:0 .4rem;
border:1px solid #d0d7de;border-radius:1rem}
"""


def _e(value: Any) -> str:
    return escape("" if value is None else str(value))


def _money(value: dict[str, str] | None) -> str:
    return "—" if value is None else f"{_e(value['amount'])} {_e(value['currency'])}"


class HtmlDocumentRenderer:
    media_type = "text/html; charset=utf-8"

    def render(self, snapshot: dict[str, Any]) -> bytes:
        trip, costs = snapshot["trip"], snapshot["costs"]
        parts = [
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
            f"<title>{_e(trip['name'])} — trip summary</title><style>{CSS}</style></head><body>",
            f"<h1>{_e(trip['name'])}</h1>",
            f"<p class=\"meta\">Trip version {_e(trip['version'])} · snapshot taken "
            f"{_e(snapshot['takenAt'])} (UTC). This document shows the trip as it was at that "
            "moment; later changes are not reflected.</p>",
            "<p>", " → ".join(_e(d) for d in trip["destinations"]),
            f" · {_e(trip['startsOn'])} to {_e(trip['endsOn'])} · {_e(trip['travelers'])} "
            f"travelers · budget {_money(trip['budget'])}</p>",
            "<h2>Itinerary</h2>",
        ]
        if snapshot["itinerary"]:
            parts.append("<table><tr><th>When</th><th>Option</th><th class=\"num\">"
                         "Quoted price</th><th>Notes</th></tr>")
            for item in snapshot["itinerary"]:
                route = (f"{_e(item['origin'])} → {_e(item['destination'])}"
                         if item["origin"] else _e(item["destination"]))
                notes = "".join(f"<div class=\"flag\">{_e(f['message'])}</div>"
                                for f in item["flags"])
                notes += "".join(f"<div>{_e(r)}</div>" for r in item["restrictions"])
                parts.append(
                    f"<tr><td>{_e(item['startsAt'])}<br>{_e(item['endsAt'])}</td>"
                    f"<td><span class=\"tag\">{_e(item['category'])}</span> "
                    f"{_e(item['title'])}<br><span class=\"meta\">{route} · "
                    f"{_e(item['providerId'])}</span></td>"
                    f"<td class=\"num\">{_money(item['quotedPrice'])}</td><td>{notes}</td></tr>")
            parts.append("</table>")
        else:
            parts.append("<p>No options in the itinerary.</p>")
        if snapshot["missing"]:
            parts.append("<h3>Missing</h3><ul>")
            parts.extend(f"<li>{_e(g['message'])}</li>" for g in snapshot["missing"])
            parts.append("</ul>")

        parts.append("<h2>Costs</h2><table><tr><th>Item</th><th>Basis</th>"
                     "<th class=\"num\">Amount</th></tr>")
        for line in costs["lines"]:
            parts.append(f"<tr><td>{_e(line['title'])}<br><span class=\"meta\">"
                         f"{_e(line['note'])}</span></td><td>{_e(line['basis'])}</td>"
                         f"<td class=\"num\">{_money(line['amount'])}</td></tr>")
        parts.append(
            f"<tr><th>Estimated</th><td></td><td class=\"num\">"
            f"{_money(costs['estimatedTotal'])}</td></tr>"
            f"<tr><th>Confirmed</th><td></td><td class=\"num\">"
            f"{_money(costs['confirmedTotal'])}</td></tr>"
            f"<tr><th>Total</th><td></td><td class=\"num\">{_money(costs['total'])}</td></tr>"
            f"<tr><th>Budget</th><td>{'over budget' if costs['overBudget'] else ''}</td>"
            f"<td class=\"num\">{_money(costs['budget'])} (remaining "
            f"{_money(costs['remaining'])})</td></tr></table>"
            f"<p class=\"meta\">{_e(costs['rounding'])}</p>")

        parts.append("<h2>Bookings</h2>")
        if snapshot["bookings"]:
            parts.append("<table><tr><th>Item</th><th>Status</th><th class=\"num\">"
                         "Accepted price</th><th>Next step</th></tr>")
            for b in snapshot["bookings"]:
                parts.append(
                    f"<tr><td>{_e(b['item'] or b['offerId'])}</td><td>{_e(b['status'])}"
                    f"{' (booked)' if b['booked'] else ''}</td><td class=\"num\">"
                    f"{_money(b['acceptedPrice'])}</td><td>{_e(b['nextStep'])}</td></tr>")
            parts.append("</table>")
        else:
            parts.append("<p>No bookings.</p>")
        parts.append("<h2>Planning rules</h2><ul>")
        parts.extend(f"<li>{_e(rule)}</li>" for rule in snapshot["rules"])
        parts.append("</ul></body></html>\n")
        return "".join(parts).encode("utf-8")
