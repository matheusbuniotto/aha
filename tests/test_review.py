"""The reviewing agent, and the loop that hands its notes back."""

from __future__ import annotations

from pathlib import Path

import pytest

from aha.fabric import AlwaysApprove, LocalRunner, TaskSpec, get
from aha.fabric.journal import Journal
from aha.fabric.review import Review
from tests.scripted import Script, call


def reviews(*verdicts: Review):
    """Replay a fixed sequence of review verdicts in place of the reviewing agent."""
    remaining = list(verdicts)

    async def fake(spec, *, diff, checks, model_override=None):
        return remaining.pop(0) if remaining else Review(approved=True, summary='fine', changes=())

    return fake


def spec_for(workspace: Path, **extra) -> TaskSpec:
    return TaskSpec(
        name='t',
        goal='write the answer down',
        workspace=workspace,
        pack='generic',
        policy=get('generic').policy(),
        **extra,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / 'work'
    path.mkdir()
    return path


def runner_for(tmp_path: Path, *turns) -> LocalRunner:
    return LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=Script(turns=list(turns)).as_model(),
    )


@pytest.mark.anyio
async def test_no_review_unless_asked(workspace: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr('aha.fabric.runner.run_review', reviews())
    runner = runner_for(tmp_path, [call('write_file', path='a.md', content='42')], 'Done.')

    outcome = await runner.run(spec_for(workspace))

    assert outcome.review is None


@pytest.mark.anyio
async def test_an_approving_review_is_recorded_and_changes_nothing(
    workspace: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        'aha.fabric.runner.run_review',
        reviews(Review(approved=True, summary='does what was asked', changes=())),
    )
    runner = runner_for(tmp_path, [call('write_file', path='a.md', content='42')], 'Done.')

    outcome = await runner.run(spec_for(workspace, review=True))

    assert outcome.review is not None and outcome.review.approved
    assert outcome.ok
    kinds = [kind for _, kind, _ in Journal(path=runner.journal_path, run_id=outcome.run_id).events()]
    assert 'reviewed' in kinds
    assert 'revising' not in kinds


@pytest.mark.anyio
async def test_requested_changes_go_back_to_the_implementer(workspace: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        'aha.fabric.runner.run_review',
        reviews(
            Review(approved=False, summary='the answer is undocumented', changes=('explain where 42 comes from',)),
            Review(approved=True, summary='now documented', changes=()),
        ),
    )
    runner = runner_for(
        tmp_path,
        [call('write_file', path='a.md', content='42')],
        'Done.',
        [call('write_file', path='a.md', content='42, from the book')],
        'Addressed the review.',
    )

    outcome = await runner.run(spec_for(workspace, review=True))

    assert outcome.review is not None and outcome.review.approved
    assert (workspace / 'a.md').read_text() == '42, from the book', 'the second pass actually ran'


@pytest.mark.anyio
async def test_the_loop_is_bounded(workspace: Path, tmp_path: Path, monkeypatch) -> None:
    """Two agents can disagree forever; a person has to break the tie eventually."""
    never_happy = Review(approved=False, summary='still not right', changes=('try again',))
    monkeypatch.setattr('aha.fabric.runner.run_review', reviews(never_happy, never_happy, never_happy))
    runner = runner_for(tmp_path, [call('write_file', path='a.md', content='42')], 'Done.')

    outcome = await runner.run(spec_for(workspace, review=True, review_rounds=1))

    assert outcome.review is not None and not outcome.review.approved
    events = [kind for _, kind, _ in Journal(path=runner.journal_path, run_id=outcome.run_id).events()]
    assert events.count('revising') == 1
    assert events.count('reviewed') == 2


@pytest.mark.anyio
async def test_unapproved_work_is_not_offered_for_review_as_a_pr(workspace: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        'aha.fabric.runner.run_review',
        reviews(Review(approved=False, summary='not ready', changes=('fix the join',))),
    )
    runner = runner_for(tmp_path, [call('write_file', path='a.md', content='42')], 'Done.')

    outcome = await runner.run(spec_for(workspace, review=True, review_rounds=0, pull_request=True))

    assert outcome.pull_request is None
    kinds = [kind for _, kind, _ in Journal(path=runner.journal_path, run_id=outcome.run_id).events()]
    assert 'publish_skipped' in kinds


def test_feedback_reads_as_the_next_piece_of_work() -> None:
    feedback = Review(
        approved=False,
        summary='the grain is wrong',
        changes=('deduplicate orders before the join', 'add a uniqueness test on order_id'),
    ).as_feedback()

    assert '1. deduplicate orders before the join' in feedback
    assert '2. add a uniqueness test on order_id' in feedback
    assert 'the grain is wrong' in feedback


@pytest.mark.anyio
async def test_a_reviewer_that_cannot_run_has_not_approved_anything(workspace: Path, tmp_path: Path) -> None:
    """Fail closed: silently approving is the one outcome that makes review worthless."""
    from aha.fabric.review import run_review

    spec = spec_for(workspace, review=True)
    review = await run_review(spec, diff='anything', checks='', model_override=None)

    assert not review.approved
    assert 'did not run' in review.summary
    assert not review.blocking, 'with no specific notes there is nothing to send back'
