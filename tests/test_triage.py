"""The cheap judgments, and the rule that they may only ever tighten."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from aha.fabric import Autonomy, LocalRunner, Risk, TaskSpec, get
from aha.fabric.classify import Classification, Triage, _level
from aha.fabric.journal import Journal
from tests.scripted import Script, call

CERTAIN = Classification(kind='model_build', confidence=0.9, source='jev')


def spec_for(tmp_path: Path, **extra) -> TaskSpec:
    return TaskSpec(
        name='t',
        goal='rebuild everything',
        workspace=tmp_path,
        pack='dbt',
        policy=get('dbt').policy(),
        **extra,
    )


def test_unknown_judgments_change_nothing(tmp_path: Path) -> None:
    """Without Jev the spec must come out exactly as the human wrote it."""
    spec = spec_for(tmp_path)

    applied = Triage(classification=CERTAIN).applied_to(spec)

    assert applied.policy == spec.policy
    assert applied.kind == 'model_build'


def test_a_destructive_request_gates_file_edits_too(tmp_path: Path) -> None:
    spec = spec_for(tmp_path)
    assert spec.policy.risk_of('write_file') is Risk.mutate

    applied = Triage(classification=CERTAIN, caution=0.9, source='jev').applied_to(spec)

    assert applied.policy.risk_of('write_file') is Risk.high
    assert applied.policy.risk_of('dbt_build') is Risk.high, 'already high stays high'
    assert applied.policy.risk_of('dbt_ls') is Risk.read, 'reads are never escalated'


def test_a_small_task_gets_a_shorter_leash(tmp_path: Path) -> None:
    applied = Triage(classification=CERTAIN, size=0.05, source='jev').applied_to(spec_for(tmp_path))

    assert applied.policy.max_steps == 20


def test_a_big_task_never_earns_a_bigger_budget(tmp_path: Path) -> None:
    """Ceilings are the human's to grant; a model judgment can only lower one."""
    spec = spec_for(tmp_path)

    applied = Triage(classification=CERTAIN, size=1.0, source='jev').applied_to(spec)

    assert applied.policy.max_steps == spec.policy.max_steps


def test_a_tight_budget_set_by_hand_is_not_raised(tmp_path: Path) -> None:
    spec = spec_for(tmp_path)
    spec = replace(spec, policy=replace(spec.policy, max_steps=5))

    applied = Triage(classification=CERTAIN, size=0.0, source='jev').applied_to(spec)

    assert applied.policy.max_steps == 5


def test_a_score_is_normalised_against_its_own_ladder() -> None:
    @dataclass
    class ScoreAnswer:
        score: float
        legend: dict[int, str]

    ladder = {0: 'low', 1: 'middle', 2: 'high'}
    assert _level(ScoreAnswer(score=2.0, legend=ladder)) == 1.0
    assert _level(ScoreAnswer(score=1.0, legend=ladder)) == 0.5
    assert _level(ScoreAnswer(score=0.0, legend=ladder)) == 0.0


def test_adjustments_are_explained_in_words() -> None:
    notes = Triage(classification=CERTAIN, caution=0.9, size=0.1, clarity=0.1, source='jev').adjustments()

    assert any('destructive' in note for note in notes)
    assert any('small' in note for note in notes)
    assert any('vague' in note for note in notes)
    assert Triage(classification=CERTAIN).adjustments() == ()


@pytest.mark.anyio
async def test_a_vague_request_is_refused_only_when_asked_for(tmp_path: Path, monkeypatch) -> None:
    """Opt-in: refusing a request the human thought was fine is the expensive mistake."""
    workspace = tmp_path / 'work'
    workspace.mkdir()

    monkeypatch.setattr(
        'aha.fabric.runner.triage',
        lambda goal, kinds: Triage(classification=CERTAIN, clarity=0.1, source='jev'),
    )
    runner = LocalRunner(
        journal_path=tmp_path / 'journal.db',
        model_override=Script(turns=[[call('write_file', path='x.sql', content='select 1')], 'Done.']).as_model(),
    )
    vague = TaskSpec(name='t', goal='make it better', workspace=workspace, autonomy=Autonomy.autonomous)

    assert (await runner.run(vague)).status == 'succeeded', 'the gate is off by default'

    outcome = await runner.run(replace(vague, policy=replace(vague.policy, min_clarity=0.3)))

    assert outcome.status == 'refused'
    assert not outcome.ok
    kinds = [kind for _, kind, _ in Journal(path=runner.journal_path, run_id=outcome.run_id).events()]
    assert kinds == ['triaged', 'refused']


@pytest.mark.anyio
async def test_a_vague_request_still_runs_when_someone_is_watching(tmp_path: Path, monkeypatch) -> None:
    """Even with the gate on: a person can be asked what they meant, an unattended run cannot."""
    workspace = tmp_path / 'work'
    workspace.mkdir()

    monkeypatch.setattr(
        'aha.fabric.runner.triage',
        lambda goal, kinds: Triage(classification=CERTAIN, clarity=0.1, source='jev'),
    )
    runner = LocalRunner(
        journal_path=tmp_path / 'journal.db',
        model_override=Script(turns=['I would need to know which model you mean.']).as_model(),
    )

    spec = TaskSpec(name='t', goal='make it better', workspace=workspace, autonomy=Autonomy.supervised)
    outcome = await runner.run(replace(spec, policy=replace(spec.policy, min_clarity=0.3)))

    assert outcome.status == 'succeeded'


def test_a_big_task_is_told_to_plan_first() -> None:
    from aha.fabric.assembly import planning_capabilities

    assert [c.id for c in planning_capabilities(decompose=True)] == ['planning', 'plan_first']
    assert [c.id for c in planning_capabilities(decompose=False)] == ['planning']


@pytest.mark.anyio
async def test_a_big_task_that_skips_the_plan_is_recorded(tmp_path: Path, monkeypatch) -> None:
    """Not a failure -- but a reviewer should know the work was never broken down."""
    from aha.fabric import AlwaysApprove
    from aha.fabric.journal import Journal

    workspace = tmp_path / 'work'
    workspace.mkdir()
    monkeypatch.setattr(
        'aha.fabric.runner.triage',
        lambda goal, kinds: Triage(classification=CERTAIN, size=0.9, source='jev'),
    )
    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=Script(turns=[[call('write_file', path='a.md', content='hi')], 'Done.']).as_model(),
    )

    outcome = await runner.run(TaskSpec(name='t', goal='refactor everything', workspace=workspace, pack='generic'))

    kinds = [kind for _, kind, _ in Journal(path=runner.journal_path, run_id=outcome.run_id).events()]
    assert 'plan_skipped' in kinds
