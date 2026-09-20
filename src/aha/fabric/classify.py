"""Deciding what kind of task this is, before spending a frontier model on it.

Classification picks the instructions and the risk posture, so it runs first and
it runs cheap. TypeSafe's Jev answers it as a typed judgment when a key is
present; otherwise a keyword rule does, and the run continues either way.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .pack import TaskKind

UNCLASSIFIED = 'unclassified'


@dataclass(frozen=True, kw_only=True)
class Classification:
    kind: str
    confidence: float
    source: str
    """Which classifier answered: `jev`, `rules`, or `default`."""

    @property
    def uncertain(self) -> bool:
        return self.confidence < 0.65


def classify(goal: str, kinds: tuple[TaskKind, ...]) -> Classification:
    """Pick the task kind. Tries Jev, falls back to rules, never raises."""
    if not kinds:
        return Classification(kind=UNCLASSIFIED, confidence=0.0, source='default')
    if os.getenv('TYPESAFE_API_KEY'):
        if found := _classify_with_jev(goal, kinds):
            return found
    return _classify_with_rules(goal, kinds)


def _classify_with_jev(goal: str, kinds: tuple[TaskKind, ...]) -> Classification | None:
    """Ask Jev for a typed choice. Returns `None` if the SDK or the call is unavailable."""
    try:
        from typesafe_sdk import Choice, TypeSafeClient
    except ImportError:
        return None

    try:
        with TypeSafeClient() as client:
            response = client.system_one(
                state={'request': goal},
                questions={
                    'kind': Choice(
                        instructions=(
                            'A data engineer asked for this work in `request`. '
                            'Which kind of task is it?'
                        ),
                        criteria={k.name: k.description for k in kinds},
                    )
                },
            )
        answer = response.choices['kind']
        confidence = float(getattr(answer, 'probability', None) or 0.75)
        return Classification(kind=answer.choice, confidence=confidence, source='jev')
    except Exception:  # noqa: BLE001 - classification must never break a run
        return None


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
