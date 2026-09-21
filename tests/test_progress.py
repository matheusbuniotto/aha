"""A run reports what it is doing while it is still doing it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from aha.fabric import AlwaysApprove, LocalRunner, TaskSpec, get
from aha.fabric.progress import ConsoleReporter, _headline, _render
from tests.scripted import Script, call


@dataclass
class SpyReporter:
    lines: list[tuple[str, str]] = field(default_factory=list)

    def phase(self, name: str, detail: str = '') -> None:
        self.lines.append(('phase', name))

    def tool(self, name: str, args: dict) -> None:
        self.lines.append(('tool', name))

    def result(self, name: str, content: str) -> None:
        self.lines.append(('result', name))

    def say(self, text: str) -> None:
        self.lines.append(('say', text.strip()))


@pytest.mark.anyio
async def test_the_run_narrates_itself_as_it_goes(tmp_path: Path) -> None:
    workspace = tmp_path / 'work'
    workspace.mkdir()
    (workspace / 'notes.md').write_text('notes\n')

    reporter = SpyReporter()
    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        reporter=reporter,
        model_override=Script(turns=[[call('read_file', path='notes.md')], 'They are notes.']).as_model(),
    )
    spec = TaskSpec(
        name='read',
        goal='explain the notes',
        workspace=workspace,
        pack='generic',
        policy=get('generic').policy(),
    )

    await runner.run(spec)

    assert ('tool', 'read_file') in reporter.lines
    assert ('result', 'read_file') in reporter.lines
    assert ('say', 'They are notes.') in reporter.lines

    phases = [name for kind, name in reporter.lines if kind == 'phase']
    assert phases[0] == 'task'
    assert 'working' in phases and 'verify' in phases
    assert phases.index('working') < phases.index('verify')


def test_tool_arguments_are_summarised_not_dumped() -> None:
    rendered = _render({'path': 'models/stg_orders.sql', 'content': 'select 1 ' * 100})

    assert rendered.startswith('path=models/stg_orders.sql')
    assert len(rendered) < 160


def test_a_result_shows_its_first_useful_line_and_the_size_of_the_rest() -> None:
    assert _headline('') == 'no output'
    assert _headline('exit=0\nran 3 models\ndone') == 'exit=0 [+2 lines]'


def test_the_console_reporter_prints_something_for_every_kind(capsys) -> None:
    reporter = ConsoleReporter()
    reporter.phase('branch', 'aha/TASK-12')
    reporter.tool('dbt_build', {'select': 'stg_orders'})
    reporter.result('dbt_build', 'exit=0')
    reporter.say('Building the model now.')

    printed = capsys.readouterr().out
    assert 'aha/TASK-12' in printed
    assert 'dbt_build' in printed
    assert 'Building the model now.' in printed


def test_tool_output_is_data_not_formatting(capsys) -> None:
    """A file containing rich markup must still print, not vanish."""
    reporter = ConsoleReporter()
    reporter.result('read_file', '[dim]version: 2')
    reporter.say('[bold] is literal here')

    printed = capsys.readouterr().out
    assert 'version: 2' in printed
    assert 'is literal here' in printed
