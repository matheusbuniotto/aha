"""Sizing up the request before spending a frontier model on it.

Four cheap judgments run first, because each one changes how the expensive part
is allowed to behave: what kind of task this is, how big it is, how much damage
a wrong answer does, and whether the request is even specific enough to act on.

TypeSafe's Jev answers all four in one typed call when a key and the SDK are
present; otherwise a keyword rule answers the first and the rest stay unknown.
Unknown means *no adjustment*: a missing classifier can neither tighten nor
loosen what the human set. Everything Jev can do is one-directional -- it may
narrow the blast radius or shorten the leash, never widen either.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from typing import Any

from .pack import TaskKind
from .spec import Policy, TaskSpec

UNCLASSIFIED = 'unclassified'

NEUTRAL_SIZE = 0.5
"""Unknown size. Neither shortens the leash nor lengthens it."""

NO_CAUTION = 0.0
"""Unknown damage. Escalating on a guess would gate everything, so it gates nothing."""

ASSUMED_CLEAR = 1.0
"""Unknown clarity. Only Jev can call a request vague; silence never blocks a run."""

SMALL = 0.25
DESTRUCTIVE = 0.66

VAGUE = 0.2
"""Below this the request is worth a warning -- never, on its own, a refusal.

Jev is asked whether an engineer could act *without asking a question first*,
which is a high bar: a fully specific request like adding a not-null test to a
named column scores around 0.65, and a real piece of work like building a mart
from two named models scores 0.16. So this number tells you how much back and
forth to expect, and refusing on it is opt-in (`Policy.min_clarity`).
"""

SMALL_TASK_STEPS = 20

SIZE_LEVELS = (
    'One file, a few lines: adding a test, fixing a typo, running a single query.',
    'A handful of files: a new model with its tests, or a bug spanning two models.',
    'A refactor across many models, or a change to how a whole layer is built.',
)

DAMAGE_LEVELS = (
    'Recoverable by editing a file: a view, a test, or documentation.',
    'Rebuilds tables other people read, or changes a contract they depend on.',
    'Drops, truncates, or full-refreshes data that is expensive or impossible to rebuild.',
)


@dataclass(frozen=True, kw_only=True)
class Classification:
    kind: str
    confidence: float
    source: str
    """Which classifier answered: `jev`, `rules`, or `default`."""

    @property
    def uncertain(self) -> bool:
        return self.confidence < 0.65

    def __str__(self) -> str:
        return f'{self.kind} ({self.confidence:.2f} via {self.source})'


@dataclass(frozen=True, kw_only=True)
class Triage:
    """What we know about a request before any of it runs."""

    classification: Classification

    size: float = NEUTRAL_SIZE
    """0 is a one-line edit, 1 is a refactor across a layer."""

    caution: float = NO_CAUTION
    """0 is recoverable by editing a file, 1 is data that is expensive to rebuild."""

    clarity: float = ASSUMED_CLEAR
    """Probability the request is specific enough to act on without asking a person."""

    source: str = 'rules'

    @property
    def vague(self) -> bool:
        """Worth mentioning. Not, by itself, worth stopping for."""
        return self.clarity < VAGUE

    def too_vague_for(self, policy: Policy) -> bool:
        """Whether the human asked for runs this unclear to be refused."""
        return self.clarity < policy.min_clarity

    def applied_to(self, spec: TaskSpec) -> TaskSpec:
        """Fold the judgments into the spec, tightening only.

        Damage raises every mutating tool to high risk, which reuses the gate
        that already exists rather than inventing a second one: more calls get
        put to whoever is answering. Size can lower a step ceiling but never
        raise one -- budgets are the human's to grant.
        """
        policy = spec.policy
        if self.caution >= DESTRUCTIVE:
            policy = policy.gating_mutations()
        if self.size <= SMALL:
            policy = replace(policy, max_steps=min(policy.max_steps, SMALL_TASK_STEPS))
        return replace(spec.with_kind(self.classification.kind), policy=policy)

    def adjustments(self) -> tuple[str, ...]:
        """What these judgments changed, in the words a supervisor would want."""
        notes = []
        if self.caution >= DESTRUCTIVE:
            notes.append(f'looks destructive ({self.caution:.2f}): every file edit now needs approval too')
        if self.size <= SMALL:
            notes.append(f'looks small ({self.size:.2f}): step budget cut to {SMALL_TASK_STEPS}')
        if self.vague:
            notes.append(f'looks vague ({self.clarity:.2f}): expect some back and forth')
        return tuple(notes)

    def __str__(self) -> str:
        if self.source != 'jev':
            return str(self.classification)
        return f'{self.classification} · size {self.size:.2f} · caution {self.caution:.2f} · clarity {self.clarity:.2f}'


def triage(goal: str, kinds: tuple[TaskKind, ...]) -> Triage:
    """Judge the request every way we cheaply can. Tries Jev, falls back to rules."""
    if not kinds:
        return Triage(classification=Classification(kind=UNCLASSIFIED, confidence=0.0, source='default'))
    if os.getenv('TYPESAFE_API_KEY') and (judged := _triage_with_jev(goal, kinds)):
        return judged
    return Triage(classification=_classify_with_rules(goal, kinds))


def classify(goal: str, kinds: tuple[TaskKind, ...]) -> Classification:
    """Just the kind, for callers that only route on it."""
    return triage(goal, kinds).classification


def _triage_with_jev(goal: str, kinds: tuple[TaskKind, ...]) -> Triage | None:
    """Ask all four questions at once. Returns `None` if the SDK or the call is unavailable.

    They are independent judgments over the same request, so Jev answers them in
    parallel in a single round trip -- the whole point of asking a small model
    instead of prompting a large one.
    """
    try:
        from typesafe_sdk import Choice, Noul, Score, TypeSafeClient
    except ImportError:
        return None

    try:
        with TypeSafeClient() as client:
            response = client.system_one(
                state={'request': goal},
                questions={
                    'kind': Choice(
                        instructions='A data engineer asked for this work in `request`. Which kind of task is it?',
                        criteria={k.name: k.description for k in kinds},
                    ),
                    'size': Score(
                        instructions='How much work is `request` for an analytics engineer?',
                        criteria=SIZE_LEVELS,
                    ),
                    'caution': Score(
                        instructions='If `request` is carried out wrongly, how bad is the damage to the warehouse?',
                        criteria=DAMAGE_LEVELS,
                    ),
                    'clear': Noul(
                        instructions=(
                            'Is `request` specific enough for an analytics engineer to act on '
                            'without asking the person who wrote it a question first?'
                        )
                    ),
                },
            )
        choice = response.choices['kind']
        return Triage(
            classification=Classification(kind=choice.choice, confidence=_confidence(choice), source='jev'),
            size=_level(response.scores['size']),
            caution=_level(response.scores['caution']),
            clarity=float(response.nouls['clear'].noul),
            source='jev',
        )
    except Exception:
        return None


def _level(answer: Any) -> float:
    """A Score is a position on its own ladder; normalise it to 0..1."""
    rungs = len(getattr(answer, 'legend', None) or ()) - 1
    return float(answer.score) / rungs if rungs > 0 else 0.0


def _confidence(answer: Any) -> float:
    """Jev reports how concentrated the distribution is; fall back to its own spread."""
    if (stated := getattr(answer, 'confidence', None)) is not None:
        return float(stated)
    spread = getattr(answer, 'probabilities', None) or {}
    return float(max(spread.values())) if spread else 0.75


def _classify_with_rules(goal: str, kinds: tuple[TaskKind, ...]) -> Classification:
    """Score each kind by its hint words, then by overlap with its description."""
    tokens = re.findall(r'[a-z_]+', goal.lower())
    words, verb = set(tokens), next(iter(tokens), '')
    best, best_score = kinds[0], 0
    for kind in kinds:
        hits = 2 * len(words & set(kind.hints))
        if verb in kind.hints:
            hits += 5
        vocabulary = {w for w in re.findall(r'[a-z_]+', kind.description.lower()) if len(w) > 4}
        hits += len(words & vocabulary)
        if hits > best_score:
            best, best_score = kind, hits
    if best_score == 0:
        return Classification(kind=kinds[0].name, confidence=0.3, source='default')
    return Classification(
        kind=best.name,
        confidence=min(0.4 + 0.1 * best_score, 0.95),
        source='rules',
    )
