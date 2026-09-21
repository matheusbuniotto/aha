"""The team's own linters, run on the agent's work."""

from __future__ import annotations

from pathlib import Path

import pytest

from aha.fabric import AlwaysApprove, LocalRunner, TaskSpec, get
from aha.fabric.checks import Check, load_checks, run_checks, summarise
from tests.scripted import Script, call


def test_a_workspace_declares_its_own_checks(tmp_path: Path) -> None:
    (tmp_path / 'aha.toml').write_text('[checks]\nlint = "echo linted"\ntypes = "echo typed"\n')

    checks = load_checks(tmp_path)

    assert {check.name for check in checks} == {'lint', 'types'}
    assert checks[0].command == ('echo', 'linted'), 'commands are argv, never a shell string'


def test_no_config_and_nothing_recognised_means_no_checks(tmp_path: Path) -> None:
    assert load_checks(tmp_path) == ()


def test_a_broken_config_does_not_take_the_run_down(tmp_path: Path) -> None:
    (tmp_path / 'aha.toml').write_text('[checks\nthis is not toml')

    assert load_checks(tmp_path) == ()


def test_results_carry_the_output_of_what_failed(tmp_path: Path) -> None:
    results = run_checks(tmp_path, (Check.of('ok', 'echo fine'), Check.of('bad', 'false')))

    assert [r.passed for r in results] == [True, False]
    assert 'FAILED bad' in summarise(results)


def test_every_check_runs_even_after_one_fails(tmp_path: Path) -> None:
    results = run_checks(tmp_path, (Check.of('bad', 'false'), Check.of('ok', 'echo fine')))

    assert len(results) == 2, 'a reviewer wants the whole list, not the first failure'


def test_a_missing_tool_fails_loudly_rather_than_silently_passing(tmp_path: Path) -> None:
    results = run_checks(tmp_path, (Check.of('nope', 'definitely-not-installed --check'),))

    assert not results[0].passed
    assert 'not installed' in results[0].output


@pytest.mark.anyio
async def test_a_failing_check_fails_the_run(tmp_path: Path) -> None:
    workspace = tmp_path / 'work'
    workspace.mkdir()
    (workspace / 'aha.toml').write_text('[checks]\nlint = "false"\n')

    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=Script(turns=[[call('write_file', path='a.md', content='hi')], 'Done.']).as_model(),
    )
    spec = TaskSpec(
        name='t',
        goal='write a note',
        workspace=workspace,
        pack='generic',
        policy=get('generic').policy(),
    )

    outcome = await runner.run(spec)

    assert outcome.status == 'succeeded', 'the agent did its part'
    assert not outcome.ok, 'but the work is not acceptable'
    assert [r.passed for r in outcome.checks] == [False]


@pytest.mark.anyio
async def test_the_check_list_is_pinned_before_the_agent_can_rewrite_it(tmp_path: Path) -> None:
    """Otherwise a run could choose the commands that judge it."""
    workspace = tmp_path / 'work'
    workspace.mkdir()
    (workspace / 'aha.toml').write_text('[checks]\nlint = "false"\n')

    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=Script(
            turns=[[call('write_file', path='aha.toml', content='[checks]\nlint = "true"\n')], 'Disarmed.']
        ).as_model(),
    )
    spec = TaskSpec(
        name='t',
        goal='pass the linter',
        workspace=workspace,
        pack='generic',
        policy=get('generic').policy(),
    )

    outcome = await runner.run(spec)

    assert not outcome.ok
    assert [r.check.command for r in outcome.checks] == [('false',)]
