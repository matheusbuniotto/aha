"""A pack is a domain plugged into the fabric: its tools, its house style, its check."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic_ai.capabilities import AgentCapability

from .spec import Policy, TaskSpec


@dataclass(frozen=True, kw_only=True)
class TaskKind:
    """One thing a pack knows how to be asked for."""

    name: str
    description: str
    """Written for a classifier to choose between. Concrete, not abstract."""

    hints: tuple[str, ...] = ()
    """Words that strongly imply this kind, for the offline classifier."""


@dataclass(frozen=True, kw_only=True)
class Verification:
    """Whether the work actually landed, judged by code rather than by the model."""

    passed: bool
    detail: str


@runtime_checkable
class Pack(Protocol):
    """What a domain has to supply to run on the fabric."""

    name: str

    def kinds(self) -> tuple[TaskKind, ...]:
        """The task vocabulary this pack answers to."""
        ...

    def policy(self) -> Policy:
        """The default blast radius for this domain. Callers may tighten it."""
        ...

    def instructions(self, spec: TaskSpec) -> str:
        """House style for this domain, specialised by `spec.kind`."""
        ...

    def capabilities(self, spec: TaskSpec) -> list[AgentCapability[None]]:
        """Domain tools, carrying `instructions()` on the capability that owns them.

        Guidance travels with the tools it governs rather than through the
        agent's system prompt, so a pack is one self-contained unit and the
        fabric never has to know what it says.
        """
        ...

    def explore(self, spec: TaskSpec) -> str:
        """A deterministic survey of what the task will touch, run before the agent.

        Cheap, read-only reconnaissance the fabric can do without spending model
        turns: which tables exist, which the request probably means, what feeds
        them. Returns the briefing, or an empty string when there is nothing
        useful to say.
        """
        ...

    def skills(self) -> Path | None:
        """A directory of portable `SKILL.md` packages, loaded on demand."""
        ...

    def verify(self, spec: TaskSpec) -> Verification:
        """Independent check that the task is done. Run after the agent stops."""
        ...


_REGISTRY: dict[str, Pack] = {}


def register(pack: Pack) -> Pack:
    """Make a pack available to the CLI by name."""
    _REGISTRY[pack.name] = pack
    return pack


def get(name: str) -> Pack:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ', '.join(sorted(_REGISTRY)) or 'none'
        raise LookupError(f'unknown pack {name!r}; registered: {known}') from None


def names() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
