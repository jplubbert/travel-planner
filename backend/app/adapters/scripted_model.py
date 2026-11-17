"""A deterministic ChatModel for tests and deterministic evaluations.

Each step receives what the model would see (the inputs of this turn and the tools offered)
and returns its turn, so scripts can react to real tool results without any model access.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.ports.assistant import ModelTurn, ToolCall, ToolResult, ToolSpec, UserText

Step = ModelTurn | Callable[["ScriptedConversation", list[UserText | ToolResult]], ModelTurn]


def call(name: str, **arguments: Any) -> ModelTurn:
    return ModelTurn(text=None, tool_calls=[ToolCall(id=f"{name}-1", name=name,
                                                     arguments=arguments)])


def say(text: str) -> ModelTurn:
    return ModelTurn(text=text)


@dataclass
class ScriptedConversation:
    model: "ScriptedModel"
    system: str
    tools: list[ToolSpec]
    history: list[tuple[str, str]]
    received: list[list[UserText | ToolResult]] = field(default_factory=list)

    async def send(self, inputs: list[UserText | ToolResult]) -> ModelTurn:
        self.received.append(inputs)
        if not self.model.steps:
            raise AssertionError("scripted model ran out of steps")
        step = self.model.steps.pop(0)
        return step(self, inputs) if callable(step) else step


class ScriptedModel:
    def __init__(self, steps: list[Step], name: str = "scripted") -> None:
        self.name = name
        self.steps = list(steps)
        self.conversations: list[ScriptedConversation] = []

    def start(self, system: str, tools: list[ToolSpec],
              history: list[tuple[str, str]]) -> ScriptedConversation:
        conversation = ScriptedConversation(self, system, tools, history)
        self.conversations.append(conversation)
        return conversation
