"""The checks a team already trusts, run after the agent stops.

`Pack.verify` answers "did the thing work" in the domain's own terms. This
answers the other half: "would this pass review here" -- sqlfluff, yamllint,
whatever the team already runs in CI. Declaring them means an agent's work is
held to the same bar as a person's.

The list is read **before** the agent starts and pinned for the run. The config
file lives in the workspace, which the agent can write to, so reading it late
would let a run choose the commands that judge it.
"""

from __future__ import annotations

import shlex
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .cmd import run

CONFIG = 'aha.toml'
MAX_OUTPUT = 4000
TIMEOUT = 300.0

WELL_KNOWN = (
    # (marker the project already has, tool, command)
    ('.sqlfluff', 'sqlfluff', 'sqlfluff lint .'),
    ('.yamllint', 'yamllint', 'yamllint .'),
)


@dataclass(frozen=True)
class Check:
    """One command that has to exit zero. Fixed argv, never a shell string."""

    name: str
    command: tuple[str, ...]

    @classmethod
    def of(cls, name: str, command: str) -> Check:
        return cls(name=name, command=tuple(shlex.split(command)))

    def __str__(self) -> str:
        return f'{self.name}: {" ".join(self.command)}'


@dataclass(frozen=True)
class CheckResult:
    check: Check
    passed: bool
    output: str

    def __str__(self) -> str:
        return f'{"ok" if self.passed else "FAILED"} {self.check.name}'


def load_checks(workspace: Path) -> tuple[Check, ...]:
    """The checks for this workspace: `aha.toml` if present, else what we recognise.

    ```toml
    [checks]
    sqlfluff = "sqlfluff lint models --dialect duckdb"
    ```
    """
    declared = _from_config(workspace)
    return declared if declared is not None else _discovered(workspace)


def run_checks(workspace: Path, checks: tuple[Check, ...]) -> tuple[CheckResult, ...]:
    """Run every check, even after one fails: a reviewer wants the whole list."""
    return tuple(_run_one(workspace, check) for check in checks)


def summarise(results: tuple[CheckResult, ...]) -> str:
    """One line per check, then the output of whichever ones failed."""
    if not results:
        return ''
    lines = [str(result) for result in results]
    lines += [f'\n--- {r.check.name}\n{r.output}' for r in results if not r.passed]
    return '\n'.join(lines)


def _run_one(workspace: Path, check: Check) -> CheckResult:
    if not check.command:
        return CheckResult(check=check, passed=False, output='empty command')
    if shutil.which(check.command[0]) is None:
        return CheckResult(check=check, passed=False, output=f'{check.command[0]} is not installed')
    done = run(workspace, *check.command, timeout=TIMEOUT)
    return CheckResult(check=check, passed=done.ok, output=done.text[-MAX_OUTPUT:])


def _from_config(workspace: Path) -> tuple[Check, ...] | None:
    """`None` when there is no config at all, which is different from an empty one."""
    path = workspace / CONFIG
    if not path.is_file():
        return None
    try:
        declared = tomllib.loads(path.read_text()).get('checks', {})
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return tuple(Check.of(name, command) for name, command in declared.items() if isinstance(command, str))


def _discovered(workspace: Path) -> tuple[Check, ...]:
    """A default worth having: run the linter the project is already configured for."""
    return tuple(
        Check.of(tool, command)
        for marker, tool, command in WELL_KNOWN
        if (workspace / marker).is_file() and shutil.which(tool)
    )
