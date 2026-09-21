"""Live feedback while a run is happening.

A run that prints nothing until it finishes is indistinguishable from a run that
has hung, so the supervisor's only move is to kill it. The journal answers
"what happened" afterwards; this answers "what is happening" now, from the same
event stream, and stays a protocol so a cloud runner can push the same lines to
a log or a Slack thread.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from rich.console import Console
from rich.markup import escape

MAX_ARG_CHARS = 60
MAX_LINE_CHARS = 100


@runtime_checkable
class Reporter(Protocol):
    """Where progress goes. A terminal, a log, a queue, or nowhere."""

    def phase(self, name: str, detail: str = '') -> None:
        """A stage of the run started or finished: explore, branch, run, verify."""
        ...

    def tool(self, name: str, args: dict[str, Any]) -> None: ...

    def result(self, name: str, content: str) -> None: ...

    def say(self, text: str) -> None:
        """Something the model said on its way through."""
        ...


class NullReporter:
    """The default. Keeps library use and tests silent."""

    def phase(self, name: str, detail: str = '') -> None: ...

    def tool(self, name: str, args: dict[str, Any]) -> None: ...

    def result(self, name: str, content: str) -> None: ...

    def say(self, text: str) -> None: ...


@dataclass
class ConsoleReporter:
    """Prints a timestamped line per event, narrow enough to skim while it runs.

    Tool output is escaped before printing: a file whose text happens to contain
    `[dim]` is data, not formatting, and rich would otherwise eat the line.
    """

    console: Console = field(default_factory=Console)
    started: float = field(default_factory=time.monotonic)

    def phase(self, name: str, detail: str = '') -> None:
        head, *rest = escape(detail).splitlines() or ['']
        self.console.print(f'[bold cyan]{name:>8}[/] {head}')
        for line in rest:
            self.console.print(f'[dim]{" " * 9}{line}[/]')

    def tool(self, name: str, args: dict[str, Any]) -> None:
        self.console.print(f'{self._clock()} [green]->[/] [bold]{name}[/] [dim]{escape(_render(args))}[/]')

    def result(self, name: str, content: str) -> None:
        self.console.print(f'{self._clock()} [dim]<- {name}: {escape(_headline(content))}[/]')

    def say(self, text: str) -> None:
        for line in text.strip().splitlines():
            if line.strip():
                self.console.print(f'{self._clock()} [yellow]..[/] {escape(_clip(line.strip(), MAX_LINE_CHARS))}')

    def _clock(self) -> str:
        elapsed = time.monotonic() - self.started
        return f'[dim]{int(elapsed) // 60:d}:{int(elapsed) % 60:02d}[/]'


def _render(args: dict[str, Any]) -> str:
    """Tool arguments on one line: enough to recognise the call, never the payload."""
    return ' '.join(f'{key}={_clip(str(value), MAX_ARG_CHARS)}' for key, value in args.items())


def _headline(content: str) -> str:
    """The first line that carries information, plus how much was left unsaid."""
    lines = [line for line in str(content).strip().splitlines() if line.strip()]
    if not lines:
        return 'no output'
    more = f' [+{len(lines) - 1} lines]' if len(lines) > 1 else ''
    return f'{_clip(lines[0].strip(), MAX_LINE_CHARS)}{more}'


def _clip(text: str, width: int) -> str:
    flat = ' '.join(text.split())
    return flat if len(flat) <= width else f'{flat[: width - 3]}...'
