"""Executing a task end to end.

`Runner` is the seam that keeps the cloud move cheap: a terminal run and a
Lambda run differ in where they execute and who approves, not in what a task is
or how it is judged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic_ai.models import Model

from . import pack as packs
from .approvals import Approver, default_approver
from .assembly import build_agent, usage_limits
from .classify import Classification, classify
from .journal import Journal
from .pack import Verification
from .spec import TaskSpec

DEFAULT_JOURNAL = Path('.aha/journal.db')


@dataclass(frozen=True, kw_only=True)
class RunOutcome:
    """Everything a supervisor needs to judge one run without reading the transcript."""

    run_id: str
    spec: TaskSpec
    classification: Classification
    output: str
    verification: Verification
    usd: float
    status: str

    @property
    def ok(self) -> bool:
        return self.status == 'succeeded' and self.verification.passed


@runtime_checkable
class Runner(Protocol):
    async def run(self, spec: TaskSpec, /) -> RunOutcome: ...


@dataclass(kw_only=True)
class LocalRunner:
    """Runs the task in this process, against the local filesystem."""

    approver: Approver | None = None
    journal_path: Path = DEFAULT_JOURNAL
    model_override: Model | str | None = None
    """Set by tests to run without a provider key."""

    async def run(self, spec: TaskSpec, /) -> RunOutcome:
        pack = packs.get(spec.pack)
        verdict = classify(spec.goal, pack.kinds())
        spec = spec.with_kind(verdict.kind)

        journal = Journal.open(self.journal_path, spec=spec)
        journal.record(
            'classified',
            kind=verdict.kind,
            confidence=verdict.confidence,
            source=verdict.source,
        )

        approver = self.approver or default_approver(spec.autonomy)
        agent = build_agent(
            spec,
            pack,
            approver=approver,
            journal=journal,
            steps_db=self.journal_path.with_name('steps.db'),
        )

        status, output, usd = 'succeeded', '', 0.0
        try:
            with agent.override(model=self.model_override) if self.model_override else _nothing():
                result = await agent.run(spec.goal, usage_limits=usage_limits(spec))
            output = str(result.output)
            usd = _cost_of(result)
        except Exception as exc:  # noqa: BLE001 - the outcome records the failure
            status, output = 'failed', f'{type(exc).__name__}: {exc}'
            journal.record('run_error', error=output)

        verification = pack.verify(spec)
        journal.record('verified', passed=verification.passed, detail=verification.detail)
        journal.finish(status=status, usd=usd, detail=verification.detail)

        return RunOutcome(
            run_id=journal.run_id,
            spec=spec,
            classification=verdict,
            output=output,
            verification=verification,
            usd=usd,
            status=status,
        )


class _nothing:
    """A context manager that does nothing, so the run path stays one shape."""

    def __enter__(self) -> None: ...

    def __exit__(self, *exc: Any) -> None: ...


def _cost_of(result: Any) -> float:
    """Best-effort cost extraction; usage accounting differs per provider."""
    try:
        usage = result.usage()
    except Exception:  # noqa: BLE001
        return 0.0
    for attr in ('cost', 'total_cost'):
        value = getattr(usage, attr, None)
        if value is not None:
            try:
                return float(value() if callable(value) else value)
            except Exception:  # noqa: BLE001
                continue
    return 0.0
