"""Running a command the fabric itself needs, with a fixed argv and no shell.

The agent's own shell access is a policy question settled in `Policy`. This is
the other kind: git and gh, run by the runner on the human's instruction, never
composed from model output.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

TIMEOUT = 120.0


@dataclass(frozen=True)
class Completed:
    ok: bool
    text: str
    """stdout and stderr together: these tools say the useful part on either."""


def run(cwd: Path, *argv: str, timeout: float = TIMEOUT) -> Completed:
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, cwd=cwd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Completed(ok=False, text=f'{argv[0]}: {exc}')
    return Completed(ok=done.returncode == 0, text=f'{done.stdout}{done.stderr}'.strip())


def git(root: Path, *args: str) -> Completed:
    """One git command in `root`."""
    return run(root, 'git', '-C', str(root), *args)
