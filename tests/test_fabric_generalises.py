"""The same fabric, a different domain: no dbt anywhere in this run.

If this passes alongside the dbt end-to-end test, the abstraction is real --
a pack supplies tools, house style, and a check, and nothing else changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aha.fabric import AlwaysApprove, Autonomy, LocalRunner, TaskSpec, get
from tests.scripted import Script, call

SCRIPT_BODY = "print('hello from the fabric')\n"


@pytest.mark.anyio
async def test_generic_pack_runs_and_verifies(tmp_path: Path) -> None:
    workspace = tmp_path / 'work'
    workspace.mkdir()

    spec = TaskSpec(
        name='hello',
        goal='create a python script that prints a greeting',
        workspace=workspace,
        pack='generic',
        autonomy=Autonomy.guarded,
        policy=get('generic').policy(),
        context={'verify_command': f'{sys.executable} hello.py'},
    )

    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=Script(
            turns=[
                [call('write_file', path='hello.py', content=SCRIPT_BODY)],
                'Wrote hello.py.',
            ]
        ).as_model(),
    )

    outcome = await runner.run(spec)

    assert outcome.classification.kind == 'create'
    assert outcome.ok, outcome.verification.detail
    assert (workspace / 'hello.py').read_text() == SCRIPT_BODY


@pytest.mark.anyio
async def test_verification_catches_a_plausible_lie(tmp_path: Path) -> None:
    """The agent says it succeeded; the verifier disagrees, and the verifier wins."""
    workspace = tmp_path / 'work'
    workspace.mkdir()

    spec = TaskSpec(
        name='broken',
        goal='create a python script that prints a greeting',
        workspace=workspace,
        pack='generic',
        autonomy=Autonomy.guarded,
        policy=get('generic').policy(),
        context={'verify_command': f'{sys.executable} hello.py'},
    )

    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=Script(
            turns=[
                [call('write_file', path='hello.py', content='print(  # unclosed\n')],
                'All done, the script works perfectly.',
            ]
        ).as_model(),
    )

    outcome = await runner.run(spec)

    assert outcome.status == 'succeeded'
    assert not outcome.verification.passed
    assert not outcome.ok
