"""A pack is a domain plugged into the fabric: its tools, its house style, its check."""

from __future__ import annotations

from dataclasses import dataclass
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
        """Domain tools. The fabric adds filesystem, shell, and the safety rails."""
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
