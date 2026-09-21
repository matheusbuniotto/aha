"""The vocabulary every task shares, independent of what the task is about."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path


class Autonomy(StrEnum):
    """How much rope the agent gets before a human has to say yes."""

    supervised = 'supervised'
    """Every mutating tool call is put to a person."""

    guarded = 'guarded'
    """Only high-risk calls are put to a person; routine edits run free."""

    autonomous = 'autonomous'
    """Nobody is watching. High-risk calls still need a pre-authorisation rule."""


class Risk(StrEnum):
    """What a tool call can cost you if it is wrong."""

    read = 'read'
    """Observes only. Always allowed."""

    mutate = 'mutate'
    """Changes files in the workspace. Recoverable via git."""

    high = 'high'
    """Touches a warehouse, the network, or anything outside the workspace."""


@dataclass(frozen=True, kw_only=True)
class Policy:
    """The blast radius. Enforced in code, never delegated to the prompt."""

    allowed_commands: tuple[str, ...] = ()
    """Executables the agent may run. An empty tuple means no shell at all."""

    protected_globs: tuple[str, ...] = (
        '.env',
        '.env.*',
        '**/profiles.yml',
        '**/*.pem',
        '**/*credentials*',
        '.git/**',
    )
    """Never readable or writable, whatever the model asks for."""

    writable_globs: tuple[str, ...] = ()
    """When set, writes are confined to these paths. Empty means the whole workspace."""

    risks: dict[str, Risk] = field(default_factory=dict)
    """Tool name to risk band. Unlisted tools are treated as `Risk.read`."""

    max_usd: float | None = 2.0
    """Hard spend ceiling for one run. `None` disables the check."""

    max_steps: int = 60
    """Model requests before the run is cut off."""

    command_timeout_s: float = 300.0
    """Wall clock limit for a single shell command."""

    min_clarity: float = 0.0
    """Refuse an unattended run whose request scores below this. Off by default.

    Opt-in because the cost of a false positive lands on the person who wrote a
    perfectly good request and got told no. Turn it on once you have seen what
    your team's requests actually score.
    """

    def risk_of(self, tool_name: str) -> Risk:
        return self.risks.get(tool_name, Risk.read)

    def gating_mutations(self) -> Policy:
        """Raise every mutating tool to high risk.

        Escalation only: reads stay reads, and a tool already high stays high.
        Used when a request looks destructive, so the existing gate catches more
        rather than a second kind of gate appearing beside it.
        """
        raised = {name: Risk.high if risk is Risk.mutate else risk for name, risk in self.risks.items()}
        return replace(self, risks=raised)

    def needs_approval(self, tool_name: str, autonomy: Autonomy) -> bool:
        """Whether this call needs a decision before it runs.

        Gating is about risk, not about who is watching. Raising autonomy
        narrows what gets gated; it never removes the gate on high-risk calls.
        Who answers the gate is the `Approver`'s job -- a person when supervised,
        a pre-authorisation rule when unattended.
        """
        risk = self.risk_of(tool_name)
        if risk is Risk.read:
            return False
        if autonomy is Autonomy.supervised:
            return True
        return risk is Risk.high


@dataclass(frozen=True, kw_only=True)
class TaskSpec:
    """One unit of work, fully described before anything runs."""

    name: str
    """Short slug identifying the task. Used in the journal and in run ids."""

    goal: str
    """What the human wants, in their own words."""

    workspace: Path
    """Root the agent is confined to. Every path it touches resolves under here."""

    pack: str = 'generic'
    """Which domain pack supplies the tools and the house style."""

    kind: str = 'unclassified'
    """Filled in by the classifier; packs use it to specialise instructions."""

    task_id: str | None = None
    """The ticket this run belongs to, such as `TASK-12`. Names the branch.

    Left unset, the run id stands in, so work is still isolated and still
    traceable back to a journal entry.
    """

    branch: bool = True
    """Whether to isolate the run on its own git branch."""

    pull_request: bool = False
    """Open a pull request when the run verifies. The review gate, not a tool gate."""

    autonomy: Autonomy = Autonomy.supervised
    model: str = 'anthropic:claude-sonnet-4-6'
    policy: Policy = field(default_factory=Policy)
    context: dict[str, str] = field(default_factory=dict)
    """Free-form extras a pack understands, such as a dbt target or a schema name."""

    def with_kind(self, kind: str) -> TaskSpec:
        return replace(self, kind=kind)

    @property
    def resolved_workspace(self) -> Path:
        return self.workspace.expanduser().resolve()
