"""The human-in-the-loop gate.

Approval is a policy decision made in code before a tool runs, not a suggestion
in the system prompt. The same gate serves a person at a terminal today and an
unattended cloud run tomorrow -- only the `Approver` changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic_ai_harness import GuardrailResult, ToolGuardrail
from pydantic_ai_harness.guardrails._tool_guardrail import ToolCallInfo

from .journal import Journal
from .spec import Autonomy, Policy, Risk


@dataclass(frozen=True, kw_only=True)
class ApprovalRequest:
    """One tool call awaiting a yes or no."""

    tool: str
    risk: Risk
    args: dict[str, object]

    def summary(self, *, width: int = 300) -> str:
        rendered = json.dumps(self.args, default=str, ensure_ascii=False)
        if len(rendered) > width:
            rendered = f'{rendered[:width]}...'
        return f'{self.tool} {rendered}'


@runtime_checkable
class Approver(Protocol):
    """Whatever decides. A terminal prompt, a Slack button, a rule, a test stub."""

    async def __call__(self, request: ApprovalRequest, /) -> bool: ...


class TerminalApprover:
    """Asks the person running the command. The default for supervised work."""

    async def __call__(self, request: ApprovalRequest, /) -> bool:
        print(f'\n  approve [{request.risk}] {request.summary()}')
        answer = input('  allow? [y/N] ').strip().lower()
        return answer in {'y', 'yes'}


class DenyUnattended:
    """Refuses anything that would need a person. The safe default when nobody is watching."""

    async def __call__(self, request: ApprovalRequest, /) -> bool:
        return False


@dataclass(frozen=True)
class PreAuthorized:
    """Standing permission for named tools, so unattended runs can still do work.

    This is how a team delegates a task to the cloud: not by turning the gate
    off, but by writing down in advance exactly which risky calls are allowed.
    """

    tools: frozenset[str]

    @classmethod
    def of(cls, *tools: str) -> PreAuthorized:
        return cls(tools=frozenset(tools))

    async def __call__(self, request: ApprovalRequest, /) -> bool:
        return request.tool in self.tools


class AlwaysApprove:
    """For tests and for trusted replays. Never wire this to a real warehouse."""

    async def __call__(self, request: ApprovalRequest, /) -> bool:
        return True


def default_approver(autonomy: Autonomy) -> Approver:
    return TerminalApprover() if autonomy is not Autonomy.autonomous else DenyUnattended()


def approval_gate(
    *,
    policy: Policy,
    autonomy: Autonomy,
    approver: Approver,
    journal: Journal | None = None,
) -> ToolGuardrail[None]:
    """A guardrail that puts risky calls to `approver` and blocks what it refuses."""

    async def guard(info: ToolCallInfo) -> bool | GuardrailResult:
        risk = policy.risk_of(info.name)
        if not policy.needs_approval(info.name, autonomy):
            if journal and risk is not Risk.read:
                journal.record('tool_allowed', tool=info.name, risk=str(risk), args=dict(info.args))
            return True

        request = ApprovalRequest(tool=info.name, risk=risk, args=dict(info.args))
        allowed = await approver(request)
        if journal:
            journal.record(
                'tool_approved' if allowed else 'tool_refused',
                tool=info.name,
                risk=str(risk),
                args=dict(info.args),
            )
        if allowed:
            return True
        return GuardrailResult.block(
            message=(
                f'A human declined the {info.name} call. Do not retry it. '
                'Either continue with a different approach or stop and explain what you need.'
            )
        )

    return ToolGuardrail(guard=guard, id='aha_approval_gate')
