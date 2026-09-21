"""Read-only querying, against whichever warehouse the dbt profile points at.

`query_sql` exists so the agent can check its own work with real numbers. That
has to stay read-only whatever the backend is, so the guard (refuse writes, wrap
in a LIMIT) lives here once and each adapter only supplies a connection.

Which adapter to use is not a second piece of configuration: the project's
`profiles.yml` already says, and following it means the tool and dbt can never
disagree about which warehouse they are talking to.
"""

from __future__ import annotations

import os
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

MAX_CHARS = 20_000
ENV_VAR = re.compile(r"{{\s*env_var\(\s*'([^']+)'\s*(?:,\s*'([^']*)')?\s*\)\s*}}")
WRITES = frozenset(
    ['insert', 'update', 'delete', 'drop', 'alter', 'create', 'attach', 'copy', 'merge', 'truncate', 'grant', 'replace']
)


@runtime_checkable
class Warehouse(Protocol):
    """Somewhere SELECTs can be run. One method, because that is all we allow."""

    name: str

    def rows(self, sql: str) -> str:
        """Run `sql` and return the rows as text, or a message explaining why not."""
        ...


def query(warehouse: Warehouse, sql: str, limit: int) -> str:
    """The only way a tool reaches a warehouse: read-only, bounded, and explained."""
    if WRITES & set(_words(sql)):
        return 'refused: query_sql is read-only; use dbt_build to change the warehouse'
    try:
        return _clip(warehouse.rows(f'SELECT * FROM ({sql.rstrip(";")}) LIMIT {int(limit)}'))
    except Exception as exc:
        return f'query failed against {warehouse.name}: {type(exc).__name__}: {exc}'


@dataclass(frozen=True)
class DuckDB:
    """A local DuckDB file, opened read-only so the process cannot write even by mistake."""

    path: Path
    name: str = 'duckdb'

    def rows(self, sql: str) -> str:
        import duckdb

        connection = duckdb.connect(str(self.path), read_only=True)
        try:
            return str(connection.sql(sql))
        finally:
            with suppress(Exception):
                connection.close()


@dataclass(frozen=True)
class Databricks:
    """A SQL warehouse over the Databricks connector.

    Credentials come from the dbt profile, so a team that can already run dbt
    against Databricks needs no extra setup. Point it at a read-only service
    principal anyway: our guard is a guard, not a permission system.
    """

    host: str
    http_path: str
    token: str
    catalog: str | None = None
    schema: str | None = None
    name: str = 'databricks'

    def rows(self, sql: str) -> str:
        from databricks import sql as connector

        with (
            connector.connect(
                server_hostname=self.host,
                http_path=self.http_path,
                access_token=self.token,
                catalog=self.catalog,
                schema=self.schema,
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description or []]
            return _table(columns, cursor.fetchall())


@dataclass(frozen=True)
class Unavailable:
    """A profile we cannot query. The agent still gets an answer it can act on."""

    reason: str
    name: str = 'none'

    def rows(self, sql: str) -> str:
        return self.reason


def open_warehouse(project: Path, target: str | None = None) -> Warehouse:
    """Build the adapter the project's profile asks for."""
    output = _output(project, target)
    if output is None:
        return Unavailable(reason='no usable profiles.yml in the project')

    kind = str(output.get('type', '')).lower()
    if kind == 'duckdb':
        path = project / str(output.get('path', ''))
        if not path.exists():
            return Unavailable(reason='no DuckDB database found yet; run dbt_build first')
        return DuckDB(path=path)
    if kind == 'databricks':
        missing = [key for key in ('host', 'http_path', 'token') if not output.get(key)]
        if missing:
            return Unavailable(reason=f'databricks profile is missing {", ".join(missing)}')
        return Databricks(
            host=str(output['host']),
            http_path=str(output['http_path']),
            token=str(output['token']),
            catalog=_optional(output.get('catalog')),
            schema=_optional(output.get('schema')),
        )
    return Unavailable(reason=f'query_sql does not support the {kind or "unknown"} adapter yet')


def _output(project: Path, target: str | None) -> dict[str, Any] | None:
    """The chosen target's connection block, with `env_var` references resolved."""
    try:
        profiles = yaml.safe_load((project / 'profiles.yml').read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None

    wanted = _profile_name(project)
    profile = profiles.get(wanted) or next((p for p in profiles.values() if isinstance(p, dict)), None)
    if not isinstance(profile, dict):
        return None

    outputs = profile.get('outputs') or {}
    chosen = outputs.get(target or profile.get('target')) or next(iter(outputs.values()), None)
    return {key: _resolve(value) for key, value in chosen.items()} if isinstance(chosen, dict) else None


def _profile_name(project: Path) -> str:
    try:
        return str((yaml.safe_load((project / 'dbt_project.yml').read_text()) or {}).get('profile', ''))
    except (OSError, yaml.YAMLError):
        return ''


def _resolve(value: Any) -> Any:
    """Expand dbt's `{{ env_var('NAME') }}`, so secrets stay in the environment."""
    if not isinstance(value, str):
        return value
    return ENV_VAR.sub(lambda m: os.getenv(m.group(1), m.group(2) or ''), value)


def _optional(value: Any) -> str | None:
    return str(value) if value else None


def _words(sql: str) -> set[str]:
    return set(re.findall(r'[a-z]+', sql.lower()))


def _table(columns: list[str], rows: list[Any]) -> str:
    header = ' | '.join(columns)
    body = '\n'.join(' | '.join(str(cell) for cell in row) for row in rows)
    return f'{header}\n{"-" * len(header)}\n{body}' if rows else f'{header}\n(no rows)'


def _clip(text: str) -> str:
    return text if len(text) <= MAX_CHARS else f'{text[:MAX_CHARS]}\n... [truncated]'
