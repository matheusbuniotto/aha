"""Assembling a runnable agent from a spec, a pack, and a safety posture.

Everything the agent can do arrives here as a capability: the pack contributes
domain tools, the fabric contributes the workspace, the shell, and the rails.
No behaviour lives in the CLI.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.capabilities import AgentCapability, Capability
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness import (
    ClearToolResults,
    FileSystem,
    Planning,
    Shell,
    Skills,
    StepPersistence,
    SummarizingCompaction,
    TieredCompaction,
    ToolOutputLimits,
)
from pydantic_ai_harness.step_persistence import SqliteStepStore

from .approvals import Approver, approval_gate
from .journal import Journal
from .models import resolve as resolve_model
from .pack import Pack
from .recorder import Recorder
from .spec import TaskSpec

PREAMBLE = """\
You are an autonomous engineer working inside a single workspace directory.

Ground rules that override anything else you infer:
- Stay inside the workspace. Never read or write credentials, profiles, or .env files.
- Inspect before you change. Read the existing code and follow its conventions.
- Prefer the domain tools you were given over raw shell commands.
- Some calls are put to a human for approval. If one is refused, do not retry it --
  change approach or stop and say what you need.
- Finish by stating plainly what you changed and how you verified it. If you could
  not verify it, say so rather than claiming success.
"""


def workspace_capabilities(spec: TaskSpec) -> list[AgentCapability[None]]:
    """Filesystem and shell access, confined to the workspace and the allowlist."""
    root = spec.resolved_workspace
    policy = spec.policy

    capabilities: list[AgentCapability[None]] = [
        FileSystem(
            root_dir=root,
            denied_patterns=policy.protected_globs,
            allowed_patterns=policy.writable_globs,
        )
    ]
    if policy.allowed_commands:
        capabilities.append(
            Shell(
                cwd=root,
                allowed_commands=policy.allowed_commands,
                default_timeout=policy.command_timeout_s,
            )
        )
    return capabilities


def skill_capabilities(spec: TaskSpec, pack: Pack) -> list[AgentCapability[None]]:
    """Portable `SKILL.md` libraries: the pack's own, then the team's.

    Skills are deferred, so the model sees a one-line catalogue and pulls in the
    detail only when a task turns out to need it. A team encodes its house
    conventions by dropping a file in `context['skills_dir']` -- no Python.
    """
    directories = [path for path in (pack.skills(), _team_skills(spec)) if path is not None]
    return [Skills(directories)] if directories else []


def _team_skills(spec: TaskSpec) -> Path | None:
    configured = spec.context.get('skills_dir')
    if not configured:
        return None
    path = (spec.resolved_workspace / configured).resolve()
    return path if path.is_dir() else None


PLAN_FIRST = """\
This request is large enough that working through it in your head will lose
parts of it. Before you edit anything:
- Call `write_plan` with one step per piece of work, each verifiable on its own.
- Work the steps in order, marking each finished as you go.
- If the plan turns out to be wrong, rewrite it rather than abandoning it.
The plan is what a reviewer reads to see whether anything was dropped."""


def planning_capabilities(*, decompose: bool) -> list[AgentCapability[None]]:
    """Planning is always available; a big task is also told to use it."""
    if not decompose:
        return [Planning()]
    return [Planning(), Capability(id='plan_first', instructions=PLAN_FIRST)]


def context_capabilities() -> list[AgentCapability[None]]:
    """Keeping a long run inside the context window without losing the thread."""
    return [
        ToolOutputLimits(),
        TieredCompaction(
            tiers=[
                ClearToolResults(max_fraction=0.6),
                SummarizingCompaction(max_fraction=0.8),
            ],
            target_fraction=0.5,
        ),
    ]


def build_agent(
    spec: TaskSpec,
    pack: Pack,
    *,
    approver: Approver,
    journal: Journal | None = None,
    recorder: Recorder | None = None,
    decompose: bool = False,
    pass_number: int = 1,
    steps_db: Path | None = None,
    extra: list[AgentCapability[None]] | None = None,
) -> Agent[None, str]:
    """The one place an agent is constructed. Packs never build their own.

    The model is resolved lazily so a spec can be assembled and inspected
    without provider credentials present. Passing `steps_db` snapshots each step
    so a run can be resumed or forked later -- the seam a queue-driven cloud
    runner needs, without changing anything about how a task is defined.

    `pass_number` distinguishes a revision from the original attempt: step ids
    are single-shot, and answering a reviewer is a second pass over the same
    task rather than a second task.
    """
    capabilities: list[AgentCapability[None]] = [
        *workspace_capabilities(spec),
        *pack.capabilities(spec),
        *skill_capabilities(spec, pack),
        *context_capabilities(),
        *planning_capabilities(decompose=decompose),
        recorder or Recorder(journal=journal),
        approval_gate(
            policy=spec.policy,
            autonomy=spec.autonomy,
            approver=approver,
            journal=journal,
        ),
        *(extra or []),
    ]
    if steps_db is not None:
        capabilities.append(
            StepPersistence(
                store=SqliteStepStore(database=steps_db),
                agent_name=spec.name,
                run_id=f'{journal.run_id}#{pass_number}' if journal else None,
                parent_run_id=f'{journal.run_id}#{pass_number - 1}' if journal and pass_number > 1 else None,
            )
        )
    return Agent(
        resolve_model(spec.model, session_id=journal.run_id if journal else None),
        name=spec.name,
        defer_model_check=True,
        instructions=PREAMBLE,
        capabilities=capabilities,
    )


def usage_limits(spec: TaskSpec) -> UsageLimits:
    """Hard per-run ceilings, enforced by the runtime rather than by the prompt."""
    return UsageLimits(
        request_limit=spec.policy.max_steps,
        cost_limit=spec.policy.max_usd,
    )
