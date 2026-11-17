"""Ports of the assistant: the language model and the tools it may call (ADR 0006).

The loop only knows these protocols. The model provider (Gemini today, a scripted model in
tests) and the tool transport (MCP over HTTP) are adapters behind them.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolSpec:
    """A tool as discovered from the MCP server (name, description, JSON Schema)."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call: ToolCall
    is_error: bool
    content: str  # what the tool returned (JSON text) or its error message


@dataclass(frozen=True)
class UserText:
    text: str


@dataclass(frozen=True)
class ModelTurn:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class ModelError(Exception):
    """The model provider failed (unavailable, quota, invalid response...)."""


class ModelConversation(Protocol):
    async def send(self, inputs: list[UserText | ToolResult]) -> ModelTurn:
        """Adds user text or tool results to the conversation and returns the model's next
        turn: final text, or tool calls to run."""
        ...


class ChatModel(Protocol):
    name: str

    def start(self, system: str, tools: list[ToolSpec],
              history: list[tuple[str, str]]) -> ModelConversation:
        """A conversation with these instructions, tools and earlier (role, text) turns."""
        ...


class AssistantTools(Protocol):
    async def list_tools(self) -> list[ToolSpec]: ...
    async def call_tool(self, call: ToolCall) -> ToolResult: ...
