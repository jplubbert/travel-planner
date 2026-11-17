"""The assistant loop end to end (chat endpoint -> loop -> MCP over HTTP -> use cases) with a
scripted model: deterministic, no model access."""

import asyncio
from typing import Any

import pytest

from app.adapters.scripted_model import ScriptedModel, call, say
from app.mcp.server import EXPECTED_TOOLS
from app.ports.assistant import ModelError, ModelTurn, ToolResult

SEARCH = {"category": "accommodation", "destination": "Lisbon",
          "starts_on": "2030-06-10", "ends_on": "2030-06-14"}


def chat(client, trip, message="Find us a hotel in Lisbon", **extra):
    return client.post(f"/trips/{trip['id']}/assistant/chat",
                       json={"message": message, **extra})


async def test_tools_are_discovered_over_mcp_and_results_flow_back(
    api, make_app, shared_trip, mock_control
):
    trip_id = shared_trip["id"]

    def answer(conversation, inputs: list[Any]) -> ModelTurn:
        [result] = inputs
        assert isinstance(result, ToolResult) and not result.is_error
        assert '"outcome":"complete"' in result.content.replace(" ", "")
        return say("The Alfama apartment costs 600.00 USD; it is a quote, not a booking.")

    model = ScriptedModel([call("get_trip", trip_id=trip_id),
                           call("search_offers", trip_id=trip_id, **SEARCH), answer])
    r = await chat(api.as_("carla", app=make_app(chat_model=model)), shared_trip)
    assert r.status_code == 200
    body = r.json()
    assert body["stopReason"] == "answered" and body["iterations"] == 3
    assert body["answer"].startswith("The Alfama apartment") and body["readOnly"] is True
    assert [c["name"] for c in body["toolCalls"]] == ["get_trip", "search_offers"]
    assert body["toolCalls"][0]["result"]["name"] == shared_trip["name"]
    assert body["toolCalls"][1]["result"]["outcome"] == "complete"

    [conversation] = model.conversations
    assert {t.name for t in conversation.tools} == EXPECTED_TOOLS  # discovered, not hard-coded
    assert trip_id in conversation.system
    assert "not instructions" in conversation.system and "never say" in conversation.system


async def test_a_failing_tool_reaches_the_model_as_an_error(api, make_app, shared_trip,
                                                            mock_control):
    for supplier in ("supplier-a", "supplier-b"):
        await mock_control.post("/scenario", json={"supplierId": supplier,
                                                   "mode": "unavailable"})
    seen: list[ToolResult] = []

    def react(conversation, inputs):
        seen.extend(inputs)
        return say("The search failed, so I cannot recommend anything right now.")

    model = ScriptedModel([call("search_offers", trip_id=shared_trip["id"], **SEARCH), react])
    body = (await chat(api.as_("ana", app=make_app(chat_model=model)), shared_trip)).json()
    assert seen[0].is_error and "failed" in seen[0].content
    assert body["toolCalls"][0]["isError"] is True
    assert body["stopReason"] == "answered"


async def test_iteration_limit_stops_the_loop(api, make_app, shared_trip):
    model = ScriptedModel([call("get_trip", trip_id=shared_trip["id"])] * 20)
    app = make_app(chat_model=model, assistant_max_iterations=3)
    body = (await chat(api.as_("ana", app=app), shared_trip)).json()
    assert body["stopReason"] == "iteration_limit" and body["iterations"] == 3
    assert len(body["toolCalls"]) == 3 and "could not finish" in body["answer"]


async def test_time_limit_stops_the_loop(api, make_app, shared_trip):
    class SlowConversation:
        async def send(self, inputs):
            await asyncio.sleep(5)
            return say("too late")

    class SlowModel:
        name = "slow"

        def start(self, system, tools, history):
            return SlowConversation()

    app = make_app(chat_model=SlowModel(), assistant_timeout_seconds=0.3)
    r = await chat(api.as_("ana", app=app), shared_trip)
    assert r.json()["stopReason"] == "time_limit" and "time limit" in r.json()["answer"]


async def test_nonparticipant_cannot_chat_and_the_model_is_never_called(api, make_app,
                                                                        shared_trip):
    model = ScriptedModel([say("should not run")])
    r = await chat(api.as_("dani", app=make_app(chat_model=model)), shared_trip)
    assert r.status_code == 404 and r.json()["code"] == "TRIP_NOT_FOUND"
    assert model.conversations == []


@pytest.mark.parametrize("user", ["ana", "beto", "carla"])
async def test_every_participant_can_chat(api, make_app, shared_trip, user):
    model = ScriptedModel([call("get_trip", trip_id=shared_trip["id"]), say("ok")])
    r = await chat(api.as_(user, app=make_app(chat_model=model)), shared_trip)
    assert r.status_code == 200 and r.json()["toolCalls"][0]["isError"] is False


async def test_the_model_cannot_reach_another_users_trip(api, make_app, shared_trip,
                                                         trip_payload):
    private = (await api.as_("dani").post("/trips", json={**trip_payload,
                                                          "name": "Dani's secret"})).json()
    model = ScriptedModel([call("get_trip", trip_id=private["id"]), say("done")])
    body = (await chat(api.as_("carla", app=make_app(chat_model=model)), shared_trip)).json()
    [result] = body["toolCalls"]
    assert result["isError"] and "not found" in result["result"]
    assert "Dani's secret" not in str(body)


async def test_write_tools_cannot_be_called(api, make_app, shared_trip):
    model = ScriptedModel([call("request_booking", trip_id=shared_trip["id"]),
                           call("add_item", trip_id=shared_trip["id"], offerId="x"),
                           say("I cannot book; please book it in the application.")])
    body = (await chat(api.as_("ana", app=make_app(chat_model=model)), shared_trip)).json()
    assert [c["isError"] for c in body["toolCalls"]] == [True, True]
    assert (await api.as_("ana").get(f"/trips/{shared_trip['id']}/bookings")).json() == []
    itinerary = (await api.as_("ana").get(f"/trips/{shared_trip['id']}/itinerary")).json()
    assert itinerary["items"] == [] and itinerary["tripVersion"] == 1


async def test_history_is_passed_and_validated(api, make_app, shared_trip):
    model = ScriptedModel([say("noted")])
    app = make_app(chat_model=model)
    r = await chat(api.as_("ana", app=app), shared_trip, message="and Madrid?",
                   history=[{"role": "user", "text": "Lisbon hotels?"},
                            {"role": "assistant", "text": "Here are some."}])
    assert r.status_code == 200
    assert model.conversations[0].history == [("user", "Lisbon hotels?"),
                                               ("assistant", "Here are some.")]
    r = await chat(api.as_("ana", app=app), shared_trip, message="")
    assert r.status_code == 422


async def test_model_failure_and_missing_configuration_are_explicit(api, make_app,
                                                                    shared_trip):
    class Broken:
        name = "broken"

        def start(self, system, tools, history):
            class Conversation:
                async def send(self, inputs):
                    raise ModelError("quota exceeded")
            return Conversation()

    r = await chat(api.as_("ana", app=make_app(chat_model=Broken())), shared_trip)
    assert r.status_code == 502 and r.json()["code"] == "ASSISTANT_MODEL_ERROR"
    r = await chat(api.as_("ana", app=make_app(gemini_api_key=None)), shared_trip)
    assert r.status_code == 503 and r.json()["code"] == "ASSISTANT_UNAVAILABLE"
