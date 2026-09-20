"""A scripted model, so the end-to-end test runs without a provider key.

Each step is one model turn: the tool calls it would make, then the text it
would finish with. Anything the real agent does -- approval, journalling,
verification -- runs exactly as it would in production.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


@dataclass
class Script:
    """Replays a fixed sequence of turns, one per model request."""

    turns: list[list[ToolCallPart] | str]
    _seen: Iterator[list[ToolCallPart] | str] = field(init=False)

    def __post_init__(self) -> None:
        self._seen = iter(self.turns)

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn = next(self._seen, 'Done.')
        if isinstance(turn, str):
            return ModelResponse(parts=[TextPart(turn)])
        return ModelResponse(parts=list(turn))

    def as_model(self) -> FunctionModel:
        return FunctionModel(self)


def call(tool: str, **args: object) -> ToolCallPart:
    return ToolCallPart(tool_name=tool, args=args)
