"""End-to-end: a dbt modelling task, run through the fabric, verified by dbt itself.

The model is scripted so this runs offline and deterministically, but everything
else is the production path -- policy, approval, journal, and verification.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aha.fabric import AlwaysApprove, Autonomy, LocalRunner, PreAuthorized, TaskSpec, get
from tests.scripted import Script, call

STG_ORDERS = """\
with source as (
    select * from {{ ref('raw_orders') }}
)

select
    id as order_id,
    user_id as customer_id,
    order_date,
    status
from source
"""

SCHEMA = """\
version: 2

models:
  - name: stg_customers
    description: One row per customer, cleaned from the raw seed.
    columns:
      - name: customer_id
        description: Primary key.
        tests: [unique, not_null]

  - name: stg_orders
    description: One row per order, cleaned from the raw seed.
    columns:
      - name: order_id
        description: Primary key.
        tests: [unique, not_null]
      - name: customer_id
        description: Customer who placed the order.
        tests:
          - relationships:
              to: ref('stg_customers')
              field: customer_id
"""


def build_script() -> Script:
    return Script(
        turns=[
            [call('write_file', path='models/staging/stg_orders.sql', content=STG_ORDERS)],
            [call('write_file', path='models/staging/schema.yml', content=SCHEMA)],
            [call('dbt_build', select='stg_orders+')],
            'Added stg_orders with a uniqueness test and a relationship to stg_customers.',
        ]
    )


def spec_for(project: Path, autonomy: Autonomy) -> TaskSpec:
    return TaskSpec(
        name='stg-orders',
        goal='add a staging model for raw orders with tests',
        workspace=project,
        pack='dbt',
        autonomy=autonomy,
        policy=get('dbt').policy(),
    )


@pytest.mark.slow
@pytest.mark.anyio
async def test_supervised_run_builds_and_verifies(project: Path, tmp_path: Path) -> None:
    runner = LocalRunner(
        approver=AlwaysApprove(),
        journal_path=tmp_path / 'journal.db',
        model_override=build_script().as_model(),
    )

    outcome = await runner.run(spec_for(project, Autonomy.supervised))

    assert outcome.classification.kind == 'model_build'
    assert outcome.status == 'succeeded', outcome.output
    assert outcome.verification.passed, outcome.verification.detail
    assert outcome.ok

    assert (project / 'models/staging/stg_orders.sql').exists()

    kinds = [kind for _, kind, _ in _journal_events(runner, outcome)]
    assert 'triaged' in kinds
    assert 'tool_approved' in kinds
    assert 'verified' in kinds


@pytest.mark.slow
@pytest.mark.anyio
async def test_unattended_run_refuses_warehouse_writes(project: Path, tmp_path: Path) -> None:
    """With nobody watching, a high-risk call is blocked rather than guessed at."""
    runner = LocalRunner(
        journal_path=tmp_path / 'journal.db',
        model_override=build_script().as_model(),
    )

    outcome = await runner.run(spec_for(project, Autonomy.autonomous))

    events = _journal_events(runner, outcome)
    refused = {p['tool'] for _, kind, p in events if kind == 'tool_refused'}
    allowed = {p['tool'] for _, kind, p in events if kind == 'tool_allowed'}

    assert 'dbt_build' in refused, 'a warehouse write must not run unattended by default'
    assert 'write_file' in allowed, 'routine workspace edits should not need a person'


@pytest.mark.slow
@pytest.mark.anyio
async def test_preauthorized_tools_run_unattended(project: Path, tmp_path: Path) -> None:
    """Delegation is granted by naming the tools in advance, not by removing the gate."""
    runner = LocalRunner(
        approver=PreAuthorized.of('dbt_build'),
        journal_path=tmp_path / 'journal.db',
        model_override=build_script().as_model(),
    )

    outcome = await runner.run(spec_for(project, Autonomy.autonomous))

    assert outcome.ok, outcome.verification.detail
    approved = {p['tool'] for _, kind, p in _journal_events(runner, outcome) if kind == 'tool_approved'}
    assert approved == {'dbt_build'}


def _journal_events(runner: LocalRunner, outcome) -> list[tuple[str, str, dict]]:
    from aha.fabric.journal import Journal

    return Journal(path=runner.journal_path, run_id=outcome.run_id).events()
