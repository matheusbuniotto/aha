"""The routing contract: which request lands on which kind.

These run offline against the rule-based classifier. Jev replaces it when
`TYPESAFE_API_KEY` is set, so this file also documents the floor Jev has to beat.
"""

from __future__ import annotations

import pytest

from aha.fabric import get
from aha.fabric.classify import classify

DBT_CASES = [
    ('create a daily revenue mart', 'model_build'),
    ('add a staging model for raw orders', 'model_build'),
    ('the orders mart is returning duplicate rows', 'bug_fix'),
    ('revenue is wrong in the daily mart', 'bug_fix'),
    ('fix the failing stg_payments model', 'bug_fix'),
    ('add not null tests to stg_orders', 'test_coverage'),
    ('what is the average order value?', 'analysis'),
    ('explain why the orders mart is slow', 'analysis'),
    ('document the customers model', 'documentation'),
]


@pytest.mark.parametrize(('goal', 'expected'), DBT_CASES)
def test_dbt_requests_route_to_the_right_kind(goal: str, expected: str) -> None:
    assert classify(goal, get('dbt').kinds()).kind == expected


def test_an_unrecognisable_request_is_flagged_uncertain() -> None:
    """Nonsense must not arrive looking like a confident decision."""
    verdict = classify('zzz qqq', get('dbt').kinds())
    assert verdict.uncertain
    assert verdict.source == 'default'


def test_classification_never_raises_without_a_vocabulary() -> None:
    verdict = classify('anything at all', ())
    assert verdict.kind == 'unclassified'


def test_jev_confidence_is_read_from_the_answer_not_invented() -> None:
    """A stated confidence must survive: a flat default hides exactly the uncertainty we want."""
    from dataclasses import dataclass

    from aha.fabric.classify import _confidence

    @dataclass
    class Stated:
        confidence: float
        probabilities: dict[str, float]

    @dataclass
    class Spread:
        probabilities: dict[str, float]

    assert _confidence(Stated(confidence=0.98, probabilities={'a': 0.99, 'b': 0.01})) == 0.98
    assert _confidence(Spread(probabilities={'a': 0.7, 'b': 0.3})) == 0.7
    assert _confidence(object()) == 0.75
