"""A second agent that reads the diff before a person is asked to.

The approval gate judges calls one at a time and `verify` judges the end state
in the domain's own terms. Neither reads the change as a whole, which is what a
reviewer does: noticing the model that was renamed but not re-pointed, the test
that passes because it asserts nothing.

It is deliberately not a rubber stamp and deliberately not a gate: it reads,
it reports, and when it asks for changes the implementing agent gets another
turn with those notes in hand. The person still decides.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai_harness import FileSystem

from .branching import Branch
from .cmd import git
from .models import resolve as resolve_model
from .spec import TaskSpec

MAX_DIFF_CHARS = 40_000

INSTRUCTIONS = """\
You are reviewing another engineer's change before it goes to a human.

You have the request, the diff, and the output of the checks that already ran.
You can read any file in the workspace for context; you cannot change anything.

Judge only what matters:
- Does the change actually do what was asked, all of it?
- Is it wrong in a way the checks would not catch -- a join that fans out, a
  test that cannot fail, a renamed model still referenced by its old name?
- Does it follow the conventions already in this project?

Style opinions, restatements of the diff, and praise are noise. If it is fine,
approve it and say so in one line. If it is not, list the specific changes
needed, each one concrete enough to act on without asking you a question.
"""


class ReviewVerdict(BaseModel):
    """What the reviewer concluded."""

    approved: bool = Field(description='True only if this is ready for a person to merge.')
    summary: str = Field(description='One or two lines: what the change does and whether it holds up.')
    changes: list[str] = Field(default_factory=list, description='Specific changes needed. Empty if approved.')


@dataclass(frozen=True)
class Review:
    approved: bool
    summary: str
    changes: tuple[str, ...]

    @property
    def blocking(self) -> bool:
        return not self.approved and bool(self.changes)

    def as_feedback(self) -> str:
        """The reviewer's notes, phrased as the next piece of work."""
        items = '\n'.join(f'{n}. {change}' for n, change in enumerate(self.changes, start=1))
        return (
            f'A reviewer read your change and asked for these before it can go to a person:\n\n{items}\n\n'
            f'Reviewer summary: {self.summary}\n\n'
            'Make exactly these changes. If one of them is wrong, say why rather than doing it anyway.'
        )

    def __str__(self) -> str:
        head = 'approved' if self.approved else f'{len(self.changes)} change(s) requested'
        return f'{head} · {self.summary}'


def diff_of(workspace, branch: Branch | None) -> str:
    """What this run changed: committed or not, against the branch it started from."""
    if branch is None:
        return git(workspace, 'diff').text[:MAX_DIFF_CHARS]
    committed = git(workspace, 'diff', f'{branch.base}...{branch.name}').text
    working = git(workspace, 'diff').text
    return '\n'.join(part for part in (committed, working) if part.strip())[:MAX_DIFF_CHARS]


def reviewer(spec: TaskSpec, *, model: str | None = None) -> Agent[None, ReviewVerdict]:
    """A read-only agent whose only output is a typed verdict.

    The verdict is asked for in the prompt rather than forced through a tool
    call: a reviewer is the natural place to put a cheap model, and cheap models
    are exactly the ones that reject a required `tool_choice` -- a reasoning
    model on opencode Go answers the question fine and refuses the mechanism.
    """
    return Agent(
        resolve_model(model or spec.model),
        name=f'{spec.name}-review',
        defer_model_check=True,
        instructions=INSTRUCTIONS,
        output_type=PromptedOutput(ReviewVerdict),
        capabilities=[
            FileSystem(
                root_dir=spec.resolved_workspace,
                denied_patterns=spec.policy.protected_globs,
                read_only=True,
            )
        ],
    )


async def run_review(
    spec: TaskSpec,
    *,
    diff: str,
    checks: str,
    model_override: Any = None,
) -> Review:
    """One review pass. Never raises: a reviewer that breaks must not fail the run."""
    agent = reviewer(spec)
    try:
        with agent.override(model=model_override) if model_override else nullcontext():
            result = await agent.run(prompt_for(spec, diff=diff, checks=checks))
    except Exception as exc:
        # Fail closed. A reviewer that cannot run has not approved anything, and
        # silently approving is the one outcome that makes the whole step worthless.
        return Review(approved=False, summary=f'review did not run: {type(exc).__name__}: {exc}', changes=())
    verdict = result.output
    return Review(
        approved=verdict.approved,
        summary=verdict.summary,
        changes=tuple(verdict.changes),
    )


def prompt_for(spec: TaskSpec, *, diff: str, checks: str) -> str:
    return (
        f'<request>\n{spec.goal}\n</request>\n\n'
        f'<checks>\n{checks.strip() or "none ran"}\n</checks>\n\n'
        f'<diff>\n{diff.strip() or "the run changed nothing"}\n</diff>'
    )
