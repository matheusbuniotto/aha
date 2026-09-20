"""A scripted model, so the end-to-end tests run without a provider key.

Each step is one model turn: the tool calls it would make, then the text it
would finish with. Everything the real agent does -- approval, journalling,
event recording, verification -- runs exactly as it would in production.

Both a plain and a streaming entry point are supplied, because capabilities
that listen to the event stream drive the model in streaming mode.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from itertools import count

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

Turn = list[ToolCallPart] | str


@dataclass
class Script:
    """Replays a fixed sequence of turns, one per model request."""

    turns: list[Turn]
    _next: count = field(init=False, default_factory=count)

    def _turn(self) -> Turn:
        index = next(self._next)
        return self.turns[index] if index < len(self.turns) else 'Done.'

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn = self._turn()
        if isinstance(turn, str):
            return ModelResponse(parts=[TextPart(turn)])
        return ModelResponse(parts=list(turn))

    async def stream(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        turn = self._turn()
        if isinstance(turn, str):
            yield turn
            return
        for index, part in enumerate(turn):
            yield {
                index: DeltaToolCall(
                    name=part.tool_name,
                    json_args=json.dumps(part.args),
                    tool_call_id=part.tool_call_id,
                )
            }

    def as_model(self) -> FunctionModel:
        return FunctionModel(self, stream_function=self.stream)


def call(tool: str, **args: object) -> ToolCallPart:
    return ToolCallPart(tool_name=tool, args=args)
