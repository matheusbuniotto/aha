"""Handing finished work to a person, as a pull request.

The tool gate asks "may this call run?"; a pull request asks "is this work any
good?". They are different questions, and the second one is the only gate a data
team actually recognises, so it stays where their review already lives.

Opt-in (`--pr`) and only on a verified run: an unverified branch is still there
to look at, it just does not get announced as ready.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .branching import Branch
from .cmd import git, run


@dataclass(frozen=True, kw_only=True)
class PullRequest:
    """Where the work went, or why it stayed put."""

    url: str | None
    detail: str

    @property
    def opened(self) -> bool:
        return self.url is not None


def publish(root: Path, *, branch: Branch, title: str, body: str) -> PullRequest:
    """Commit the branch, push it, and open a pull request against its base."""
    if not git(root, 'status', '--porcelain').text and not _ahead(root, branch):
        return PullRequest(url=None, detail='nothing to publish: the run changed no tracked files')

    git(root, 'add', '--all')
    committed = git(root, 'commit', '--message', f'{title}\n\n{body}')
    if not committed.ok and 'nothing to commit' not in committed.text:
        return PullRequest(url=None, detail=f'commit failed: {committed.text}')

    pushed = git(root, 'push', '--set-upstream', 'origin', branch.name)
    if not pushed.ok:
        return PullRequest(url=None, detail=f'committed locally; push failed: {pushed.text}')

    if shutil.which('gh') is None:
        return PullRequest(url=None, detail='pushed; install the gh CLI to open the pull request')

    opened = run(
        root,
        'gh',
        'pr',
        'create',
        '--base',
        branch.base,
        '--head',
        branch.name,
        '--title',
        title,
        '--body',
        body,
    )
    if not opened.ok:
        return PullRequest(url=None, detail=f'pushed; gh pr create failed: {opened.text}')
    return PullRequest(url=opened.text.splitlines()[-1].strip(), detail='opened')


def summary(*, goal: str, run_id: str, kind: str, verification: str, tools: dict[str, int]) -> str:
    """The pull request body: what was asked, what ran, and how it was checked."""
    used = ', '.join(f'{name} x{count}' for name, count in sorted(tools.items())) or 'none'
    return (
        f'{goal}\n\n'
        f'Run `{run_id}`, classified as `{kind}`.\n\n'
        f'**Tools used**: {used}\n\n'
        f'**Verification**\n```\n{verification.strip()}\n```\n\n'
        'Opened by an agent run. Review before merging.'
    )


def _ahead(root: Path, branch: Branch) -> bool:
    """Whether the branch already carries commits its base does not."""
    done = git(root, 'rev-list', '--count', f'{branch.base}..{branch.name}')
    return done.ok and done.text.strip() not in {'', '0'}
