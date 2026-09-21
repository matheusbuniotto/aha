"""The generic pack: any task, proving the fabric is not dbt-shaped.

It contributes no domain tools -- only house style and a verification command --
so it is also the smallest worked example of what a pack has to supply.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai.capabilities import AgentCapability, Capability

from ..fabric.pack import TaskKind, Verification, register
from ..fabric.spec import Policy, Risk, TaskSpec

KINDS = (
    TaskKind(
        name='edit',
        description='Change existing files, refactor code, or apply a described modification.',
        hints=('change', 'edit', 'refactor', 'update', 'modify', 'rename', 'fix'),
    ),
    TaskKind(
        name='create',
        description='Write a new file, script, module, or document from scratch.',
        hints=('create', 'write', 'new', 'add', 'generate', 'scaffold'),
    ),
    TaskKind(
        name='investigate',
        description='Read and explain code or data without changing anything.',
        hints=('explain', 'investigate', 'why', 'understand', 'review', 'find', 'where'),
    ),
)


def default_policy() -> Policy:
    return Policy(
        allowed_commands=('git', 'ls', 'cat', 'python', 'pytest', 'uv'),
        risks={
            'write_file': Risk.mutate,
            'edit_file': Risk.mutate,
            'create_directory': Risk.mutate,
            'run_command': Risk.high,
        },
    )


@dataclass(frozen=True)
class GenericPack:
    """Filesystem and shell work with no domain knowledge attached."""

    name: str = 'generic'

    def kinds(self) -> tuple[TaskKind, ...]:
        return KINDS

    def policy(self) -> Policy:
        return default_policy()

    def instructions(self, spec: TaskSpec) -> str:
        if spec.kind == 'investigate':
            return 'Investigate and report. Do not modify any file.'
        return (
            'Make the change the request asks for, following the conventions already '
            'present in the workspace. Keep the diff as small as the task allows.'
        )

    def capabilities(self, spec: TaskSpec) -> list[AgentCapability[None]]:
        """No domain tools -- only house style, which still travels as a capability."""
        return [Capability(id='generic', instructions=self.instructions(spec))]

    def explore(self, spec: TaskSpec) -> str:
        """Nothing to survey: with no domain model, the filesystem is the map."""
        return ''

    def skills(self) -> Path | None:
        return None

    def verify(self, spec: TaskSpec) -> Verification:
        """Run `context['verify_command']` if one was supplied; otherwise trust nothing."""
        command = spec.context.get('verify_command')
        if not command:
            return Verification(passed=True, detail='no verify_command configured')
        done = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            cwd=Path(spec.resolved_workspace),
            timeout=600,
        )
        detail = (done.stdout + done.stderr).strip()[-800:]
        return Verification(passed=done.returncode == 0, detail=detail)


register(GenericPack())
