"""Assembling a runnable agent from a spec, a pack, and a safety posture.

Everything the agent can do arrives here as a capability: the pack contributes
domain tools, the fabric contributes the workspace, the shell, and the rails.
No behaviour lives in the CLI.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.capabilities import AgentCapability
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness import (
    ClearToolResults,
    FileSystem,
    Planning,
    Shell,
    StepPersistence,
    SummarizingCompaction,
    TieredCompaction,
    ToolOutputLimits,
)
from pydantic_ai_harness.step_persistence import SqliteStepStore

from .approvals import Approver, approval_gate
from .journal import Journal
from .pack import Pack
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
        Planning(),
    ]


def build_agent(
    spec: TaskSpec,
    pack: Pack,
    *,
    approver: Approver,
    journal: Journal | None = None,
    steps_db: Path | None = None,
    extra: list[AgentCapability[None]] | None = None,
) -> Agent[None, str]:
    """The one place an agent is constructed. Packs never build their own.

    The model is resolved lazily so a spec can be assembled and inspected
    without provider credentials present. Passing `steps_db` snapshots each step
    so a run can be resumed or forked later -- the seam a queue-driven cloud
    runner needs, without changing anything about how a task is defined.
    """
    capabilities: list[AgentCapability[None]] = [
        *workspace_capabilities(spec),
        *pack.capabilities(spec),
        *context_capabilities(),
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
                run_id=journal.run_id if journal else None,
            )
        )
    return Agent(
        spec.model,
        name=spec.name,
        defer_model_check=True,
        instructions=f'{PREAMBLE}\n{pack.instructions(spec)}',
        capabilities=capabilities,
    )


def usage_limits(spec: TaskSpec) -> UsageLimits:
    """Hard per-run ceilings, enforced by the runtime rather than by the prompt."""
    return UsageLimits(
        request_limit=spec.policy.max_steps,
        cost_limit=spec.policy.max_usd,
    )
