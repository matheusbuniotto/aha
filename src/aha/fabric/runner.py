"""Executing a task end to end.

`Runner` is the seam that keeps the cloud move cheap: a terminal run and a
Lambda run differ in where they execute and who approves, not in what a task is
or how it is judged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic_ai.models import Model

from . import pack as packs
from .approvals import Approver, default_approver
from .assembly import build_agent, usage_limits
from .branching import Branch, ensure_branch
from .classify import Classification, Triage, triage
from .journal import Journal
from .pack import Pack, Verification
from .progress import NullReporter, Reporter
from .publish import PullRequest, publish, summary
from .recorder import Recorder
from .spec import Autonomy, TaskSpec

DEFAULT_JOURNAL = Path('.aha/journal.db')


@dataclass(frozen=True, kw_only=True)
class RunOutcome:
    """Everything a supervisor needs to judge one run without reading the transcript."""

    run_id: str
    spec: TaskSpec
    triage: Triage
    output: str
    verification: Verification
    usd: float
    status: str
    branch: str | None = None
    """The branch the work landed on, or None when the workspace is not a repo."""

    pull_request: PullRequest | None = None
    """Set when `--pr` was asked for; carries the url or why there is none."""

    @property
    def ok(self) -> bool:
        return self.status == 'succeeded' and self.verification.passed

    @property
    def classification(self) -> Classification:
        return self.triage.classification


@dataclass
class _Attempt:
    """How far the agent got. Separate from the verdict, which is code's to give."""

    status: str = 'succeeded'
    output: str = ''
    usd: float = 0.0
    branch: Branch | None = None


@runtime_checkable
class Runner(Protocol):
    async def run(self, spec: TaskSpec, /) -> RunOutcome: ...


@dataclass(kw_only=True)
class LocalRunner:
    """Runs the task in this process, against the local filesystem."""

    approver: Approver | None = None
    journal_path: Path = DEFAULT_JOURNAL
    reporter: Reporter = field(default_factory=NullReporter)
    """Where progress goes while the run is still happening."""

    model_override: Model | str | None = None
    """Set by tests to run without a provider key."""

    async def run(self, spec: TaskSpec, /) -> RunOutcome:
        pack = packs.get(spec.pack)
        verdict = triage(spec.goal, pack.kinds())
        spec = verdict.applied_to(spec)

        journal = Journal.open(self.journal_path, spec=spec)
        journal.record(
            'triaged',
            kind=verdict.classification.kind,
            confidence=verdict.classification.confidence,
            size=verdict.size,
            caution=verdict.caution,
            clarity=verdict.clarity,
            source=verdict.source,
            max_steps=spec.policy.max_steps,
        )
        self.reporter.phase('task', f'{journal.run_id} · {verdict}')

        for note in verdict.adjustments():
            self.reporter.phase('policy', note)

        if refusal := self._refuse(spec, verdict, journal):
            return refusal

        recorder = Recorder(journal=journal, reporter=self.reporter)
        attempt = await self._attempt(spec, pack, journal, recorder)

        self.reporter.phase('verify', 'checking the work independently of what the model claims')
        verification = pack.verify(spec)
        journal.record('verified', passed=verification.passed, detail=verification.detail)

        landed = attempt.status == 'succeeded' and verification.passed
        pull_request = self._publish(spec, journal, attempt.branch, verification, recorder.counts) if landed else None
        journal.finish(status=attempt.status, usd=attempt.usd, detail=verification.detail)

        return RunOutcome(
            run_id=journal.run_id,
            spec=spec,
            triage=verdict,
            output=attempt.output,
            verification=verification,
            usd=attempt.usd,
            status=attempt.status,
            branch=attempt.branch.name if attempt.branch else None,
            pull_request=pull_request,
        )

    def _refuse(self, spec: TaskSpec, verdict: Triage, journal: Journal) -> RunOutcome | None:
        """Stop an unattended run the human said was too unclear to be worth starting.

        Off unless `Policy.min_clarity` is set. With a person at the terminal a
        vague goal costs a conversation; with nobody watching it costs a branch
        full of confident guesses -- but refusing good work is worse than either,
        so the threshold is the human's to choose.
        """
        if not (verdict.too_vague_for(spec.policy) and spec.autonomy is Autonomy.autonomous):
            return None

        detail = (
            f'clarity {verdict.clarity:.2f} is below the {spec.policy.min_clarity:.2f} this run required; '
            'name the models or columns you mean, or run it supervised'
        )
        journal.record('refused', reason=detail)
        journal.finish(status='refused', detail=detail)
        self.reporter.phase('refused', detail)
        return RunOutcome(
            run_id=journal.run_id,
            spec=spec,
            triage=verdict,
            output=detail,
            verification=Verification(passed=False, detail='not run'),
            usd=0.0,
            status='refused',
        )

    async def _attempt(self, spec: TaskSpec, pack: Pack, journal: Journal, recorder: Recorder) -> _Attempt:
        """Everything that can go wrong, in one place, so every failure is journalled.

        Branching and exploration live in here with the model call for that
        reason: a misconfigured workspace should land as a failed run a
        supervisor can read, not as a traceback.
        """
        attempt = _Attempt()
        try:
            attempt.branch = self._branch(spec, journal)
            prompt = self._brief(spec, pack, journal)
            agent = build_agent(
                spec,
                pack,
                approver=self.approver or default_approver(spec.autonomy),
                journal=journal,
                recorder=recorder,
                steps_db=self.journal_path.with_name('steps.db'),
            )
            self.reporter.phase('working', spec.goal)
            with agent.override(model=self.model_override) if self.model_override else _nothing():
                result = await agent.run(prompt, usage_limits=usage_limits(spec))
            attempt.output = str(result.output)
            attempt.usd = _cost_of(result)
        except Exception as exc:
            attempt.status, attempt.output = 'failed', f'{type(exc).__name__}: {exc}'
            journal.record('run_error', error=attempt.output)
            self.reporter.phase('error', attempt.output)
        return attempt

    def _publish(
        self,
        spec: TaskSpec,
        journal: Journal,
        branch: Branch | None,
        verification: Verification,
        tools: dict[str, int],
    ) -> PullRequest | None:
        """Announce verified work for review, when the human asked for a review."""
        if not spec.pull_request:
            return None
        if branch is None:
            return PullRequest(url=None, detail='no branch to publish: the workspace is not a git repository')

        result = publish(
            spec.resolved_workspace,
            branch=branch,
            title=f'{spec.task_id or spec.name}: {spec.goal}'[:100],
            body=summary(
                goal=spec.goal,
                run_id=journal.run_id,
                kind=spec.kind,
                verification=verification.detail,
                tools=tools,
            ),
        )
        journal.record('published', url=result.url, detail=result.detail)
        self.reporter.phase('review', result.url or result.detail)
        return result

    def _branch(self, spec: TaskSpec, journal: Journal) -> Branch | None:
        """Isolate the run on its own branch, so one ticket is one reviewable diff."""
        if not spec.branch:
            return None
        branch = ensure_branch(spec.resolved_workspace, spec.task_id or journal.run_id)
        if branch is None:
            self.reporter.phase('branch', 'workspace is not a git repository; changes are not isolated')
            journal.record('branch_skipped', reason='not a git repository')
            return None
        journal.set_branch(branch.name)
        journal.record('branched', branch=branch.name, base=branch.base, created=branch.created)
        verb = 'created from' if branch.created else 'reusing, based on'
        self.reporter.phase('branch', f'{branch.name} ({verb} {branch.base})')
        return branch

    def _brief(self, spec: TaskSpec, pack: Pack, journal: Journal) -> str:
        """Survey the workspace first, and hand the agent the goal with what we found."""
        brief = pack.explore(spec).strip()
        if not brief:
            return spec.goal
        journal.record('explored', brief=brief)
        self.reporter.phase('explore', brief)
        return f'{spec.goal}\n\n<survey>\n{brief}\n</survey>'


class _nothing:
    """A context manager that does nothing, so the run path stays one shape."""

    def __enter__(self) -> None: ...

    def __exit__(self, *exc: Any) -> None: ...


def _cost_of(result: Any) -> float:
    """Best-effort cost extraction; usage accounting differs per provider."""
    try:
        usage = result.usage()
    except Exception:
        return 0.0
    for attr in ('cost', 'total_cost'):
        value = getattr(usage, attr, None)
        if value is not None:
            try:
                return float(value() if callable(value) else value)
            except Exception:
                continue
    return 0.0
