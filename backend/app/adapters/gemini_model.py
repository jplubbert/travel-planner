"""ChatModel adapter for Google Gemini with the official SDK (google-genai).

The conversation keeps the SDK's own ``Content`` objects: Gemini 3 models require their
previous turns (including thought signatures) to be sent back unchanged when continuing a
function-calling exchange. Automatic function calling is disabled: the application's
loop decides which tools run.

Transient API errors (rate limit, overload, and the sporadic 403 seen with valid keys)
are retried once after a short wait; client errors such as 400/401/402 are not, because
the same request would fail again. The assistant loop's overall timeout still applies.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from google import genai
from google.genai import errors, types

from app.ports.assistant import ModelError, ModelTurn, ToolCall, ToolResult, ToolSpec, UserText

RETRYABLE_STATUS = frozenset({403, 429, 500, 502, 503, 504})
Sleep = Callable[[float], Awaitable[None]]


class GeminiConversation:
    def __init__(self, client: genai.Client, model: str, config: types.GenerateContentConfig,
                 history: list[tuple[str, str]], retry_delay_seconds: float = 1.0,
                 sleep: Sleep = asyncio.sleep) -> None:
        self._client = client
        self._model = model
        self._config = config
        self._retry_delay = retry_delay_seconds
        self._sleep = sleep
        self._contents: list[types.Content] = [
            types.Content(role="model" if role == "assistant" else "user",
                          parts=[types.Part(text=text)])
            for role, text in history
        ]

    async def send(self, inputs: list[UserText | ToolResult]) -> ModelTurn:
        parts = []
        for item in inputs:
            if isinstance(item, UserText):
                parts.append(types.Part(text=item.text))
            else:
                key = "error" if item.is_error else "result"
                part = types.Part.from_function_response(name=item.call.name,
                                                         response={key: item.content})
                if part.function_response is not None:
                    part.function_response.id = item.call.id
                parts.append(part)
        self._contents.append(types.Content(role="user", parts=parts))
        response = await self._generate()
        if not response.candidates or response.candidates[0].content is None:
            raise ModelError("Gemini returned no candidate (possibly blocked).")
        content = response.candidates[0].content
        self._contents.append(content)  # unchanged: keeps thought signatures
        calls = [ToolCall(id=c.id or f"call-{n}", name=c.name or "", arguments=dict(c.args or {}))
                 for n, c in enumerate(response.function_calls or [])]
        text = "".join(p.text for p in content.parts or [] if p.text and not p.thought)
        return ModelTurn(text=text or None, tool_calls=calls)

    async def _generate(self) -> types.GenerateContentResponse:
        for attempt in (1, 2):
            try:
                return await self._client.aio.models.generate_content(
                    model=self._model, contents=self._contents, config=self._config)
            except errors.APIError as exc:
                if attempt == 1 and exc.code in RETRYABLE_STATUS:
                    await self._sleep(self._retry_delay)
                    continue
                raise ModelError(f"Gemini API error {exc.code}: {exc.message}") from exc
        raise AssertionError("unreachable")


class GeminiChatModel:
    def __init__(self, api_key: str, model: str, temperature: float = 0.2,
                 thinking_level: str | None = "LOW", client: Any = None,
                 retry_delay_seconds: float = 1.0, sleep: Sleep = asyncio.sleep) -> None:
        self.name = model
        self._client = client or genai.Client(api_key=api_key)
        self._temperature = temperature
        self._retry_delay = retry_delay_seconds
        self._sleep = sleep
        # gemini-3.8-flash supports LOW/MEDIUM/HIGH; MINIMAL is rejected by that model.
        self._thinking = (types.ThinkingConfig(thinking_level=types.ThinkingLevel(
            thinking_level.upper())) if thinking_level else None)

    def start(self, system: str, tools: list[ToolSpec],
              history: list[tuple[str, str]]) -> GeminiConversation:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self._temperature,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description,
                                          parameters_json_schema=t.input_schema)
                for t in tools])] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=self._thinking,
        )
        return GeminiConversation(self._client, self.name, config, history,
                                  self._retry_delay, self._sleep)
