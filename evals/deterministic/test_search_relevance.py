"""Search relevance over the provider fixtures, through the MCP search tool.

For each query the expected offers are written by hand from mock/provider_mock/fixtures.py.
Precision: every returned offer satisfies the query (category, destination/origin, party,
inside the window). Recall: every expected offer is returned. Both must be 1.0.
"""

from datetime import date, datetime

import pytest

from evals.support import call, payload

CASES = [
    # (id, arguments, expected fixture templates for a party of 2)
    ("lisbon-stays", {"category": "accommodation", "destination": "Lisbon",
                      "starts_on": "2030-06-10", "ends_on": "2030-06-14"},
     {"lis-hotel-central", "lis-apartment-alfama", "lis-hostel-baixa", "lis-hotel-belem"}),
    ("lisbon-activities-11th", {"category": "activity", "destination": "lisbon",
                                "starts_on": "2030-06-11", "ends_on": "2030-06-11"},
     {"lis-tram-tour", "lis-sintra-day"}),
    ("london-to-lisbon", {"category": "transport", "destination": "Lisbon", "origin": "London",
                          "starts_on": "2030-06-10", "ends_on": "2030-06-10"},
     {"lis-flight-lon-am", "lis-flight-lon-pm"}),
    ("madrid-to-paris", {"category": "transport", "destination": "PARIS", "origin": "madrid",
                         "starts_on": "2030-06-18", "ends_on": "2030-06-18"},
     {"par-train-mad", "par-flight-mad-am", "par-flight-mad-pm"}),
    ("paris-stays", {"category": "accommodation", "destination": "Paris",
                     "starts_on": "2030-06-18", "ends_on": "2030-06-22"},
     {"par-hotel-marais", "par-apartment-montmartre", "par-hotel-latin", "par-hostel-canal"}),
    ("madrid-activities", {"category": "activity", "destination": "Madrid",
                           "starts_on": "2030-06-14", "ends_on": "2030-06-18"},
     {"mad-prado-tour", "mad-toledo-day", "mad-flamenco", "mad-tapas-walk"}),
    ("no-stay-fits-window", {"category": "accommodation", "destination": "Lisbon",
                             "starts_on": "2030-06-11", "ends_on": "2030-06-13"}, set()),
    ("unknown-destination", {"category": "activity", "destination": "Reykjavik"}, set()),
]


def satisfies(offer: dict, args: dict, travelers: int) -> bool:
    starts = date.fromisoformat(args.get("starts_on", "2030-06-10"))
    ends = date.fromisoformat(args.get("ends_on", "2030-06-22"))
    return (offer["category"] == args["category"]
            and offer["destination"].casefold() == args["destination"].casefold()
            and (args["category"] != "transport"
                 or offer["origin"].casefold() == args["origin"].casefold())
            and offer["travelers"] == travelers
            and starts <= datetime.fromisoformat(offer["startsAt"]).date()
            and datetime.fromisoformat(offer["endsAt"]).date() <= ends)


@pytest.mark.parametrize("case_id,args,expected", CASES, ids=[c[0] for c in CASES])
async def test_search_precision_and_recall(mcp_as, shared_trip, mock_control, case_id, args,
                                           expected):
    async with mcp_as("carla") as tools:
        result = payload(await call(tools, "search_offers", trip_id=shared_trip["id"], **args))
    returned = {o["offerId"].removeprefix("offer-").removesuffix("-p2"): o
                for o in result["offers"]}
    relevant = [o for o in returned.values() if satisfies(o, args, shared_trip["travelers"])]
    precision = len(relevant) / len(returned) if returned else 1.0
    recall = len(expected & returned.keys()) / len(expected) if expected else 1.0
    print(f"{case_id}: returned={len(returned)} precision={precision:.2f} recall={recall:.2f}")
    assert precision == 1.0 and recall == 1.0, (sorted(returned), sorted(expected))
    assert result["outcome"] == ("complete" if expected else "empty")
