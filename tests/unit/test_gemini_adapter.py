"""GeminiChatModel against a fake google-genai client: request shape and response mapping,
without network or API key."""

from typing import Any

import pytest
from google.genai import errors, types

from app.adapters.gemini_model import GeminiChatModel
from app.ports.assistant import ModelError, ToolCall, ToolResult, ToolSpec, UserText

SPEC = ToolSpec("get_trip", "Trip details", {
    "type": "object", "properties": {"trip_id": {"type": "string"}}, "required": ["trip_id"]})


class FakeModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        # Snapshot the contents: the adapter keeps appending to the same list.
        self.calls.append({**kwargs, "contents": list(kwargs["contents"])})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.aio = type("Aio", (), {})()
        self.aio.models = FakeModels(responses)


def response(*parts: types.Part) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=list(parts)))])


async def test_tool_call_round_trip_keeps_model_content_and_ids():
    model_call = types.Part(function_call=types.FunctionCall(
        id="c1", name="get_trip", args={"trip_id": "t1"}), thought_signature=b"sig")
    client = FakeClient([response(model_call),
                         response(types.Part(text="thinking", thought=True),
                                  types.Part(text="Your trip is set."))])
    model = GeminiChatModel("unused", "gemini-3.8-flash", client=client)
    conversation = model.start("system rules", [SPEC], [("user", "hi"), ("assistant", "hello")])

    turn = await conversation.send([UserText("What is my trip?")])
    assert turn.tool_calls == [ToolCall("c1", "get_trip", {"trip_id": "t1"})]
    first = client.aio.models.calls[0]
    assert first["model"] == "gemini-3.8-flash"
    config = first["config"]
    assert config.system_instruction == "system rules"
    assert config.automatic_function_calling.disable is True
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    [declaration] = config.tools[0].function_declarations
    assert declaration.name == "get_trip"
    assert declaration.parameters_json_schema == SPEC.input_schema
    assert [c.role for c in first["contents"]] == ["user", "model", "user"]

    final = await conversation.send([ToolResult(turn.tool_calls[0], False, '{"name":"T"}')])
    assert final.text == "Your trip is set." and final.tool_calls == []  # thoughts excluded
    second = client.aio.models.calls[1]["contents"]
    assert second[3].parts[0].thought_signature == b"sig"  # model turn sent back unchanged
    function_response = second[4].parts[0].function_response
    assert (function_response.name, function_response.id) == ("get_trip", "c1")
    assert function_response.response == {"result": '{"name":"T"}'}


async def test_tool_errors_are_sent_as_errors():
    client = FakeClient([response(types.Part(text="ok"))])
    conversation = GeminiChatModel("k", "m", client=client).start("s", [SPEC], [])
    await conversation.send([ToolResult(ToolCall("c", "get_trip", {}), True, "Trip not found")])
    sent = client.aio.models.calls[0]["contents"][-1].parts[0].function_response
    assert sent.response == {"error": "Trip not found"}


@pytest.mark.parametrize("failure", [
    errors.APIError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}),
    types.GenerateContentResponse(candidates=[]),
])
async def test_provider_failures_become_model_errors(failure):
    client = FakeClient([failure])
    conversation = GeminiChatModel("k", "m", client=client).start("s", [], [])
    with pytest.raises(ModelError):
        await conversation.send([UserText("hi")])
