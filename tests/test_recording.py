"""The audit trail covers the whole run, not only the calls our gate ruled on."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from aha.fabric import AlwaysApprove, Autonomy, LocalRunner, TaskSpec, get
from aha.fabric.journal import Journal
from aha.fabric.recorder import _args_of
from tests.scripted import Script, call


@pytest.mark.anyio
async def test_journal_records_calls_the_approval_gate_never_sees(tmp_path: Path) -> None:
    workspace = tmp_path / 'work'
    workspace.mkdir()
    (workspace / 'notes.md').write_text('some existing notes\n')

    spec = TaskSpec(
        name='read-then-write',
        goal='investigate the notes and explain them',
        workspace=workspace,
        pack='generic',
        autonomy=Autonomy.guarded,
        policy=get('generic').policy(),
    )

    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=Script(
            turns=[
                [call('read_file', path='notes.md')],
                'They are notes.',
            ]
        ).as_model(),
    )

    outcome = await runner.run(spec)
    events = Journal(path=runner.journal_path, run_id=outcome.run_id).events()
    by_kind = {kind for _, kind, _ in events}

    # read_file is Risk.read, so the approval gate is never consulted for it.
    assert 'tool_allowed' not in by_kind
    assert 'tool_call' in by_kind
    assert 'tool_result' in by_kind

    calls = [p['tool'] for _, kind, p in events if kind == 'tool_call']
    assert 'read_file' in calls


def test_streamed_tool_arguments_are_recorded_as_fields() -> None:
    """A streaming model delivers arguments as JSON text; the journal wants fields."""

    @dataclass
    class Part:
        args: object

    assert _args_of(Part(args='{"path": "models/stg_orders.sql"}')) == {'path': 'models/stg_orders.sql'}
    assert _args_of(Part(args={'path': 'a.sql'})) == {'path': 'a.sql'}
    assert _args_of(Part(args='not json')) == {'raw': 'not json'}
