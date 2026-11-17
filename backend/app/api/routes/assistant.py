"""US-11 assistant chat. Any participant may ask; the assistant only reads, through the
read-only MCP tools, acting for the requesting user."""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictStr

from app.adapters.mcp_client import mcp_tools
from app.api.deps import ClockDep, Viewer
from app.api.errors import error_response
from app.api.schemas import Input
from app.assistant.loop import Limits, run_assistant
from app.mcp.identity import issue_token
from app.ports.assistant import ModelError

router = APIRouter()


class Turn(Input):
    role: Literal["user", "assistant"]
    text: Annotated[StrictStr, Field(max_length=4000)]


class ChatBody(Input):
    message: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    history: Annotated[list[Turn], Field(max_length=20)] = []


class ToolCallOut(BaseModel):
    name: str
    arguments: dict[str, Any]
    isError: bool
    result: Any  # parsed JSON when the tool returned JSON, else text


class ChatOut(BaseModel):
    answer: str
    stopReason: Literal["answered", "iteration_limit", "time_limit"]
    toolCalls: list[ToolCallOut]
    iterations: int
    model: str
    readOnly: Literal[True] = True


def _parsed(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


@router.post("/trips/{trip_id}/assistant/chat", responses={503: {}, 502: {}})
async def chat(body: ChatBody, access: Viewer, clock: ClockDep,
               request: Request) -> Any:
    state = request.app.state
    model = state.chat_model
    if model is None:
        return error_response(request, 503, "ASSISTANT_UNAVAILABLE",
                              "The assistant is not configured (GEMINI_API_KEY is not set).")
    settings = state.settings
    token = issue_token(settings.mcp_token_secret, access.user_id, clock.now(),
                        settings.mcp_token_ttl_seconds)
    failure: ModelError | None = None
    async with mcp_tools(request.app, token) as tools:
        # Caught inside the MCP session: its task group would wrap it in an ExceptionGroup.
        try:
            run = await run_assistant(
                model, tools, str(access.trip_id), body.message,
                [(t.role, t.text) for t in body.history],
                Limits(settings.assistant_max_iterations, settings.assistant_timeout_seconds),
                clock.today())
        except ModelError as exc:
            failure = exc
    if failure is not None:
        return error_response(request, 502, "ASSISTANT_MODEL_ERROR",
                              f"The language model failed: {failure}")
    out = ChatOut(
        answer=run.answer, stopReason=run.stop_reason, iterations=run.iterations,
        model=model.name,
        toolCalls=[ToolCallOut(name=r.call.name, arguments=r.call.arguments,
                               isError=r.is_error, result=_parsed(r.content))
                   for r in run.tool_results])
    return JSONResponse(out.model_dump(mode="json"))
