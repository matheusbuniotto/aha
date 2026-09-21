"""Read-only querying, whichever warehouse the dbt profile names."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aha.packs.warehouse import Databricks, DuckDB, Unavailable, open_warehouse, query

DATABRICKS_PROFILE = """\
shop:
  target: prod
  outputs:
    prod:
      type: databricks
      host: dbc-123.cloud.databricks.com
      http_path: /sql/1.0/warehouses/abc
      token: "{{ env_var('DATABRICKS_TOKEN') }}"
      catalog: analytics
      schema: marts
"""


@pytest.fixture
def databricks_project(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / 'dbt_project.yml').write_text('name: shop\nprofile: shop\n')
    (tmp_path / 'profiles.yml').write_text(DATABRICKS_PROFILE)
    monkeypatch.setenv('DATABRICKS_TOKEN', 'secret-token')
    return tmp_path


class Fake:
    """A warehouse that records the SQL it was handed."""

    name = 'fake'

    def __init__(self) -> None:
        self.seen = ''

    def rows(self, sql: str) -> str:
        self.seen = sql
        return 'ok'


def test_writes_are_refused_before_any_connection_is_opened() -> None:
    warehouse = Fake()

    for sql in ('delete from customers', 'DROP TABLE orders', 'create or replace view v as select 1'):
        assert query(warehouse, sql, 50).startswith('refused:')

    assert warehouse.seen == '', 'a rejected statement must never reach the warehouse'


def test_every_query_is_bounded() -> None:
    warehouse = Fake()

    query(warehouse, 'select * from customers;', 10)

    assert warehouse.seen == 'SELECT * FROM (select * from customers) LIMIT 10'


def test_a_broken_warehouse_explains_itself_rather_than_raising() -> None:
    class Broken:
        name = 'broken'

        def rows(self, sql: str) -> str:
            raise TimeoutError('warehouse is asleep')

    answer = query(Broken(), 'select 1', 1)

    assert 'query failed against broken' in answer
    assert 'warehouse is asleep' in answer


def test_the_profile_chooses_the_adapter(databricks_project: Path) -> None:
    warehouse = open_warehouse(databricks_project)

    assert isinstance(warehouse, Databricks)
    assert warehouse.http_path == '/sql/1.0/warehouses/abc'
    assert warehouse.catalog == 'analytics'
    assert warehouse.token == 'secret-token', 'env_var references must resolve'


def test_a_databricks_profile_missing_credentials_says_which(tmp_path: Path) -> None:
    (tmp_path / 'dbt_project.yml').write_text('name: shop\nprofile: shop\n')
    (tmp_path / 'profiles.yml').write_text(
        textwrap.dedent("""\
        shop:
          target: prod
          outputs:
            prod:
              type: databricks
              host: dbc-123.cloud.databricks.com
        """)
    )

    warehouse = open_warehouse(tmp_path)

    assert isinstance(warehouse, Unavailable)
    assert 'http_path' in warehouse.rows('select 1')


def test_duckdb_needs_the_database_to_exist(project: Path) -> None:
    assert isinstance(open_warehouse(project), Unavailable)

    (project / 'jaffle.duckdb').write_bytes(b'')
    assert isinstance(open_warehouse(project), DuckDB)


def test_an_adapter_we_do_not_support_says_so(tmp_path: Path) -> None:
    (tmp_path / 'dbt_project.yml').write_text('name: shop\nprofile: shop\n')
    (tmp_path / 'profiles.yml').write_text('shop:\n  target: dev\n  outputs:\n    dev:\n      type: snowflake\n')

    assert 'snowflake' in open_warehouse(tmp_path).rows('select 1')
