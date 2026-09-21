"""The audit trail covers the whole run, not only the calls our gate ruled on."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.scripted import Script, call

from aha.fabric import AlwaysApprove, Autonomy, LocalRunner, TaskSpec, get
from aha.fabric.journal import Journal


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
