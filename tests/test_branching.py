"""Every run lands on its own branch, named after the ticket."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aha.fabric import AlwaysApprove, LocalRunner, TaskSpec, branch_name, ensure_branch, get
from aha.fabric.journal import Journal
from tests.scripted import Script, call


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    workspace = tmp_path / 'repo'
    workspace.mkdir()
    (workspace / 'notes.md').write_text('notes\n')
    for args in (
        ('init', '--initial-branch', 'main'),
        ('config', 'user.email', 'test@example.com'),
        ('config', 'user.name', 'test'),
        ('add', '.'),
        ('commit', '-m', 'initial'),
    ):
        subprocess.run(['git', '-C', str(workspace), *args], check=True, capture_output=True)
    return workspace


def current_branch(repo: Path) -> str:
    done = subprocess.run(
        ['git', '-C', str(repo), 'rev-parse', '--abbrev-ref', 'HEAD'],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_branch_is_named_after_the_task_id() -> None:
    assert branch_name('TASK-12') == 'aha/TASK-12'
    assert branch_name('feat: add orders!') == 'aha/feat-add-orders'


def test_ensure_branch_creates_then_reuses(repo: Path) -> None:
    first = ensure_branch(repo, 'TASK-12')
    assert first is not None
    assert (first.name, first.base, first.created) == ('aha/TASK-12', 'main', True)
    assert current_branch(repo) == 'aha/TASK-12'

    subprocess.run(['git', '-C', str(repo), 'switch', 'main'], check=True, capture_output=True)
    again = ensure_branch(repo, 'TASK-12')
    assert again is not None and not again.created
    assert current_branch(repo) == 'aha/TASK-12'


def test_a_workspace_without_git_still_runs(tmp_path: Path) -> None:
    """A scratch directory is a legitimate place to work; it just has no undo button."""
    assert ensure_branch(tmp_path, 'TASK-12') is None


@pytest.mark.anyio
async def test_run_isolates_itself_on_the_task_branch(repo: Path, tmp_path: Path) -> None:
    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=Script(turns=[[call('write_file', path='answer.md', content='42\n')], 'Wrote it.']).as_model(),
    )
    spec = TaskSpec(
        name='answer',
        goal='write the answer down',
        workspace=repo,
        pack='generic',
        task_id='TASK-12',
        policy=get('generic').policy(),
    )

    outcome = await runner.run(spec)

    assert outcome.branch == 'aha/TASK-12'
    assert current_branch(repo) == 'aha/TASK-12'
    assert (repo / 'answer.md').exists()

    events = Journal(path=runner.journal_path, run_id=outcome.run_id).events()
    branched = [p for _, kind, p in events if kind == 'branched']
    assert branched and branched[0]['base'] == 'main'


@pytest.mark.anyio
async def test_without_a_task_id_the_run_id_names_the_branch(repo: Path, tmp_path: Path) -> None:
    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=Script(turns=['Nothing to do.']).as_model(),
    )
    spec = TaskSpec(name='survey', goal='look around', workspace=repo, pack='generic')

    outcome = await runner.run(spec)

    assert outcome.branch == f'aha/{outcome.run_id}'
