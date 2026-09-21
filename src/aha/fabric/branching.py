"""Every run gets its own branch, named after the task it belongs to.

The branch is the real undo button: the approval gate decides what may happen,
git decides how cheaply it can be unhappened. Isolating the run also means a
reviewer reads one diff per ticket, which is what makes unattended work
delegatable at all. Created by the runner, not by the agent -- `.git/**` stays
protected so the agent cannot rewrite its own history.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PREFIX = 'aha/'
UNSAFE = re.compile(r'[^A-Za-z0-9._-]+')


@dataclass(frozen=True, kw_only=True)
class Branch:
    """Where the run's changes will land."""

    name: str
    base: str
    created: bool
    """False when the branch already existed and we switched onto it."""


def slug(task_id: str) -> str:
    """A task id as git will accept it: `TASK-12` stays `TASK-12`."""
    cleaned = UNSAFE.sub('-', task_id).strip('-.')
    return cleaned or 'task'


def branch_name(task_id: str) -> str:
    return f'{PREFIX}{slug(task_id)}'


def ensure_branch(root: Path, task_id: str) -> Branch | None:
    """Switch the workspace onto the task's branch, creating it if needed.

    Returns `None` when the workspace is not a git repository: a scratch
    directory is still a legitimate place to run, it just has no undo button.
    """
    if not _git(root, 'rev-parse', '--git-dir').ok:
        return None

    name = branch_name(task_id)
    base = _current_branch(root)
    if name == base:
        return Branch(name=name, base=base, created=False)

    exists = _git(root, 'rev-parse', '--verify', '--quiet', f'refs/heads/{name}').ok
    switch = _git(root, 'switch', name) if exists else _git(root, 'switch', '--create', name)
    if not switch.ok:
        raise RuntimeError(f'could not switch to {name}: {switch.text}')
    return Branch(name=name, base=base, created=not exists)


def _current_branch(root: Path) -> str:
    done = _git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
    return done.text if done.ok else 'HEAD'


@dataclass(frozen=True)
class _Result:
    ok: bool
    text: str


def _git(root: Path, *args: str) -> _Result:
    done = subprocess.run(
        ['git', '-C', str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return _Result(ok=done.returncode == 0, text=f'{done.stdout}{done.stderr}'.strip())
