"""The review gate: verified work is committed, pushed, and offered for review."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aha.fabric import AlwaysApprove, LocalRunner, TaskSpec, ensure_branch, get
from aha.fabric.journal import Journal
from aha.fabric.publish import publish, summary
from tests.scripted import Script, call


def git(root: Path, *args: str) -> str:
    done = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A repo with a real (bare, local) origin, so pushing is exercised for real."""
    origin = tmp_path / 'origin.git'
    subprocess.run(['git', 'init', '--bare', '-b', 'main', str(origin)], check=True, capture_output=True)

    work = tmp_path / 'work'
    work.mkdir()
    (work / 'notes.md').write_text('notes\n')
    for args in (
        ('init', '--initial-branch', 'main'),
        ('config', 'user.email', 'test@example.com'),
        ('config', 'user.name', 'test'),
        ('remote', 'add', 'origin', str(origin)),
        ('add', '.'),
        ('commit', '-m', 'initial'),
        ('push', '-u', 'origin', 'main'),
    ):
        git(work, *args)
    return work


def test_a_run_that_changed_nothing_is_not_published(clone: Path) -> None:
    branch = ensure_branch(clone, 'TASK-1')
    assert branch is not None

    result = publish(clone, branch=branch, title='nothing', body='nothing')

    assert not result.opened
    assert 'nothing to publish' in result.detail


def test_work_is_committed_and_pushed_before_review(clone: Path) -> None:
    branch = ensure_branch(clone, 'TASK-2')
    assert branch is not None
    (clone / 'answer.md').write_text('42\n')

    result = publish(clone, branch=branch, title='TASK-2: write the answer', body='body')

    assert git(clone, 'status', '--porcelain') == '', 'the run should leave a clean tree'
    assert 'TASK-2: write the answer' in git(clone, 'log', '-1', '--pretty=%s')
    assert git(clone, 'rev-parse', 'aha/TASK-2') == git(clone, 'rev-parse', 'origin/aha/TASK-2')
    # gh cannot reach a local bare remote, so the url is absent and the reason is not.
    assert not result.opened
    assert 'pushed' in result.detail


def test_the_body_says_what_ran_and_how_it_was_checked() -> None:
    body = summary(
        goal='add a staging model',
        run_id='task-1234',
        kind='model_build',
        verification='exit=0\nPASS=4',
        tools={'dbt_build': 2, 'write_file': 1},
    )

    assert 'add a staging model' in body
    assert 'dbt_build x2' in body
    assert 'PASS=4' in body
    assert 'Review before merging' in body


@pytest.mark.anyio
async def test_an_unverified_run_is_never_offered_for_review(clone: Path, tmp_path: Path) -> None:
    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=Script(turns=[[call('write_file', path='answer.md', content='42\n')], 'Done.']).as_model(),
    )
    spec = TaskSpec(
        name='answer',
        goal='write the answer down',
        workspace=clone,
        pack='generic',
        task_id='TASK-3',
        pull_request=True,
        policy=get('generic').policy(),
        context={'verify_command': 'false'},
    )

    outcome = await runner.run(spec)

    assert not outcome.ok
    assert outcome.pull_request is None, 'work that failed its check is not announced as ready'
    assert 'published' not in {kind for _, kind, _ in Journal(path=runner.journal_path, run_id=outcome.run_id).events()}


@pytest.mark.anyio
async def test_a_verified_run_is_published_and_journalled(clone: Path, tmp_path: Path) -> None:
    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=Script(turns=[[call('write_file', path='answer.md', content='42\n')], 'Done.']).as_model(),
    )
    spec = TaskSpec(
        name='answer',
        goal='write the answer down',
        workspace=clone,
        pack='generic',
        task_id='TASK-4',
        pull_request=True,
        policy=get('generic').policy(),
        context={'verify_command': 'true'},
    )

    outcome = await runner.run(spec)

    assert outcome.ok
    assert outcome.pull_request is not None
    assert git(clone, 'rev-parse', 'aha/TASK-4') == git(clone, 'rev-parse', 'origin/aha/TASK-4')
    assert 'published' in {kind for _, kind, _ in Journal(path=runner.journal_path, run_id=outcome.run_id).events()}
