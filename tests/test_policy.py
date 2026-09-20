"""The safety rails, checked without a model in the loop."""

from __future__ import annotations

from aha.fabric.spec import Autonomy, Policy, Risk

POLICY = Policy(risks={'write_file': Risk.mutate, 'dbt_build': Risk.high, 'dbt_ls': Risk.read})


def test_reads_never_need_approval() -> None:
    for autonomy in Autonomy:
        assert not POLICY.needs_approval('dbt_ls', autonomy)


def test_supervised_gates_every_mutation() -> None:
    assert POLICY.needs_approval('write_file', Autonomy.supervised)
    assert POLICY.needs_approval('dbt_build', Autonomy.supervised)


def test_guarded_gates_only_high_risk() -> None:
    assert not POLICY.needs_approval('write_file', Autonomy.guarded)
    assert POLICY.needs_approval('dbt_build', Autonomy.guarded)


def test_autonomy_never_removes_the_high_risk_gate() -> None:
    """Raising autonomy narrows what is gated; it never ungates a high-risk call."""
    for autonomy in Autonomy:
        assert POLICY.needs_approval('dbt_build', autonomy)


def test_unknown_tools_are_treated_as_reads() -> None:
    assert POLICY.risk_of('something_new') is Risk.read
