"""Exploration: the table candidates and the lineage, found before the model guesses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aha.fabric import TaskSpec, get
from aha.packs.explore import Manifest, describe, survey

MANIFEST = {
    'nodes': {
        'seed.jaffle.raw_orders': {
            'name': 'raw_orders',
            'original_file_path': 'seeds/raw_orders.csv',
            'description': '',
            'columns': {},
        },
        'model.jaffle.stg_orders': {
            'name': 'stg_orders',
            'original_file_path': 'models/staging/stg_orders.sql',
            'description': 'One row per order.',
            'columns': {'order_id': {}, 'customer_id': {}, 'status': {}},
        },
        'model.jaffle.customers': {
            'name': 'customers',
            'original_file_path': 'models/marts/customers.sql',
            'description': 'Customer lifetime value.',
            'columns': {'customer_id': {}, 'lifetime_value': {}},
        },
    },
    'sources': {
        'source.jaffle.raw.payments': {
            'name': 'payments',
            'source_name': 'raw',
            'original_file_path': 'models/sources.yml',
            'description': 'Stripe payments.',
            'columns': {'payment_id': {}},
        }
    },
    'parent_map': {
        'model.jaffle.stg_orders': ['seed.jaffle.raw_orders'],
        'model.jaffle.customers': ['model.jaffle.stg_orders'],
    },
    'child_map': {
        'seed.jaffle.raw_orders': ['model.jaffle.stg_orders'],
        'model.jaffle.stg_orders': ['model.jaffle.customers'],
    },
}


@pytest.fixture
def manifest(tmp_path: Path) -> Manifest:
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(MANIFEST))
    loaded = Manifest.load(path)
    assert loaded is not None
    return loaded


def test_survey_names_candidates_and_their_lineage(manifest: Manifest) -> None:
    brief = survey(manifest, 'the orders staging model is dropping rows')

    assert 'stg_orders' in brief
    assert 'models/staging/stg_orders.sql' in brief
    assert 'upstream: raw_orders' in brief
    assert 'downstream: customers' in brief


def test_a_column_match_finds_a_table_the_goal_never_named(manifest: Manifest) -> None:
    """The request says 'lifetime value'; the table is called `customers`."""
    brief = survey(manifest, 'check the lifetime_value numbers')

    assert 'customers' in brief


def test_find_searches_names_descriptions_and_columns(manifest: Manifest) -> None:
    assert [r.name for r in manifest.find('payment')] == ['raw.payments']
    assert [r.name for r in manifest.find('stripe')] == ['raw.payments']
    assert [r.name for r in manifest.find('customer_id')] == ['customers', 'stg_orders']


def test_describe_renders_lineage_for_the_agent(manifest: Manifest) -> None:
    rendered = describe(manifest, manifest.find('stg_orders'))

    assert 'stg_orders (model)' in rendered
    assert 'columns: order_id, customer_id, status' in rendered
    assert describe(manifest, []) == 'no match'


def test_a_goal_about_nothing_that_exists_says_so(manifest: Manifest) -> None:
    assert 'probably new work' in survey(manifest, 'build a subscriptions model')


@pytest.mark.slow
def test_dbt_pack_surveys_a_real_project(project: Path) -> None:
    """End to end against dbt's own manifest, parsed on demand."""
    spec = TaskSpec(name='survey', goal='fix the customers mart', workspace=project, pack='dbt')

    brief = get('dbt').explore(spec)

    assert 'customers' in brief
    assert 'upstream:' in brief
