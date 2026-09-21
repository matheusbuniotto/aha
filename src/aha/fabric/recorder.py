"""Recording what the whole agent did, not just what our own gate saw.

The approval gate only observes calls it is asked to rule on. Compaction,
planning, filesystem writes, and every built-in capability pass it by, so a
journal built from the gate alone has blind spots exactly where a supervisor
would look. This listens to the run's event stream instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import CapabilityEvent, RunContext
from pydantic_ai.capabilities import AbstractCapability, on_event
from pydantic_ai.messages import PartEndEvent, TextPart, ToolCallEvent, ToolResultEvent

from .journal import Journal
from .progress import NullReporter, Reporter

MAX_RESULT_CHARS = 2000


@dataclass
class Recorder(AbstractCapability[None]):
    """Writes every tool call, tool result, and capability event to the journal.

    The same stream feeds the live reporter, so what a supervisor watches and
    what the audit trail keeps can never drift apart.
    """

    journal: Journal | None = None
    reporter: Reporter = field(default_factory=NullReporter)
    id: str | None = 'aha_recorder'
    description: str | None = None
    defer_loading: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    """Per-tool call tallies, cheap enough to keep in memory for the run summary."""

    @on_event(ToolCallEvent)
    async def _on_tool_call(self, ctx: RunContext[None], event: ToolCallEvent) -> None:
        name = getattr(event.part, 'tool_name', 'unknown')
        self.counts[name] = self.counts.get(name, 0) + 1
        args = _args_of(event.part)
        self.reporter.tool(name, args)
        self._write('tool_call', tool=name, args=args)

    @on_event(ToolResultEvent)
    async def _on_tool_result(self, ctx: RunContext[None], event: ToolResultEvent) -> None:
        name = getattr(event.part, 'tool_name', 'unknown')
        content = _clip(getattr(event.part, 'content', ''))
        self.reporter.result(name, content)
        self._write('tool_result', tool=name, content=content)

    @on_event(PartEndEvent)
    async def _on_part_end(self, ctx: RunContext[None], event: PartEndEvent) -> None:
        """The model's own narration: the only signal of intent between tool calls."""
        if isinstance(event.part, TextPart) and event.part.content.strip():
            self.reporter.say(event.part.content)
            self._write('model_text', content=_clip(event.part.content))

    @on_event(CapabilityEvent)
    async def _on_capability_event(self, ctx: RunContext[None], event: CapabilityEvent) -> None:
        self._write(
            'capability_event',
            capability=event.capability_id,
            event_kind=event.event_kind,
            tool=event.tool_name,
        )

    def _write(self, kind: str, **payload: Any) -> None:
        if self.journal is not None:
            self.journal.record(kind, **payload)


def _args_of(part: Any) -> dict[str, Any]:
    """Arguments as a mapping. A streamed call carries them as a JSON string."""
    args = getattr(part, 'args', None)
    if isinstance(args, dict):
        return args
    try:
        parsed = json.loads(args)
    except (TypeError, ValueError):
        return {'raw': _clip(args)}
    return parsed if isinstance(parsed, dict) else {'raw': _clip(args)}


def _clip(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= MAX_RESULT_CHARS else f'{text[:MAX_RESULT_CHARS]}...'
