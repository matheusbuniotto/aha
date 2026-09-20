"""The dbt pack: analytics engineering as a task the fabric can run.

dbt is invoked as a subprocess with an explicit argument vector -- never through
a shell -- so the agent chooses from a fixed set of verbs and flags rather than
composing a command line.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai.capabilities import AgentCapability, Capability

from ..fabric.pack import Pack, TaskKind, Verification, register
from ..fabric.spec import Policy, Risk, TaskSpec

MAX_OUTPUT_CHARS = 20_000

KINDS = (
    TaskKind(
        name='model_build',
        description='Create or change a dbt model, a staging table, or a mart, and make it compile and run.',
        hints=('model', 'staging', 'build', 'create', 'stg', 'incremental', 'materialize'),
    ),
    TaskKind(
        name='test_coverage',
        description='Add or repair dbt tests, schema assertions, uniqueness, not null, and accepted values.',
        hints=('test', 'tests', 'assert', 'unique', 'coverage', 'validation'),
    ),
    TaskKind(
        name='bug_fix',
        description='A model is failing, wrong, or broken. Diagnose the error and repair the SQL.',
        hints=('fix', 'bug', 'broken', 'failing', 'fails', 'error', 'wrong', 'debug', 'repair',
               'duplicate', 'duplicates', 'missing', 'mismatch', 'incorrect', 'unexpected'),
    ),
    TaskKind(
        name='analysis',
        description='Answer a question about the data by querying tables and summarising numbers. No model changes.',
        hints=('how', 'many', 'much', 'question', 'analyse', 'analyze', 'report', 'explain',
               'count', 'average', 'trend', 'why', 'compare', 'top', 'distribution'),
    ),
    TaskKind(
        name='documentation',
        description='Write or update model descriptions, column documentation, and yml metadata.',
        hints=('document', 'documentation', 'describe', 'description', 'docs', 'comment'),
    ),
)

INSTRUCTIONS = {
    'model_build': """\
Build the model the request asks for.
- Put sources in `models/staging` as `stg_<source>__<entity>.sql`; put business
  concepts in `models/marts`.
- Reference other models with `{{ ref('...') }}` and sources with `{{ source('...') }}`.
  Never hardcode a schema or a database name.
- Add the model to a `schema.yml` with a description and at least one test on its key.
- Run `dbt_build` on your model's selector until it passes.""",
    'test_coverage': """\
Strengthen the test suite.
- Every model needs a uniqueness and a not-null test on its grain.
- Prefer built-in generic tests before writing singular tests.
- Run `dbt_test` and make sure the new tests actually pass before you stop.""",
    'bug_fix': """\
Repair the failure.
- Reproduce it first with `dbt_build` or `dbt_test` and read the real error.
- Fix the cause, not the symptom. Do not delete a failing test to make it pass.
- Re-run the same selector to prove it is fixed.""",
    'analysis': """\
Answer the question with data.
- Use `query_sql` against the warehouse; do not modify any model.
- Show the numbers you based the answer on, and state the grain and the filters.""",
    'documentation': """\
Document the models.
- Describe what the model means to the business, not what the SQL does.
- Document every column. Keep descriptions in the model's `schema.yml`.
- Run `dbt_parse` to confirm the yml is still valid.""",
}


def default_policy() -> Policy:
    """dbt's blast radius: warehouse writes are high risk, file edits are routine."""
    return Policy(
        allowed_commands=('git',),
        risks={
            'write_file': Risk.mutate,
            'edit_file': Risk.mutate,
            'create_directory': Risk.mutate,
            'run_command': Risk.mutate,
            'dbt_run': Risk.high,
            'dbt_build': Risk.high,
            'dbt_seed': Risk.high,
            'query_sql': Risk.read,
            'dbt_test': Risk.read,
            'dbt_compile': Risk.read,
            'dbt_parse': Risk.read,
            'dbt_ls': Risk.read,
        },
    )


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return f'{text[:half]}\n\n... [{len(text) - MAX_OUTPUT_CHARS} chars elided] ...\n\n{text[-half:]}'


def _dbt(project: Path, *args: str, timeout: float = 600.0) -> str:
    """Invoke dbt with a fixed argv and return its combined output."""
    executable = shutil.which('dbt')
    argv = [executable] if executable else [sys.executable, '-m', 'dbt.cli.main']
    argv += [*args, '--project-dir', str(project), '--profiles-dir', str(project)]
    try:
        done = subprocess.run(  # noqa: S603 - argv is fixed, never shell
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=project,
        )
    except subprocess.TimeoutExpired:
        return f'dbt {args[0]} timed out after {timeout:.0f}s'
    body = _clip(f'{done.stdout}\n{done.stderr}'.strip())
    return f'exit={done.returncode}\n{body}'


@dataclass(frozen=True)
class DbtPack:
    """Analytics engineering tasks against a local dbt project."""

    name: str = 'dbt'

    def kinds(self) -> tuple[TaskKind, ...]:
        return KINDS

    def policy(self) -> Policy:
        return default_policy()

    def instructions(self, spec: TaskSpec) -> str:
        target = spec.context.get('target', 'dev')
        body = INSTRUCTIONS.get(spec.kind, INSTRUCTIONS['model_build'])
        return (
            f'You are an analytics engineer working in a dbt project at the workspace root.\n'
            f'The dbt target is `{target}`.\n\n{body}'
        )

    def capabilities(self, spec: TaskSpec) -> list[AgentCapability[None]]:
        project = spec.resolved_workspace

        def dbt_ls(select: str = '') -> str:
            """List the resources in the project, optionally narrowed by a dbt selector."""
            args = ['ls'] + (['--select', select] if select else [])
            return _dbt(project, *args)

        def dbt_parse() -> str:
            """Parse the project and report any yml or Jinja errors. Changes nothing."""
            return _dbt(project, 'parse')

        def dbt_compile(select: str = '') -> str:
            """Compile models to SQL without running them. Use this to inspect generated SQL."""
            args = ['compile'] + (['--select', select] if select else [])
            return _dbt(project, *args)

        def dbt_build(select: str = '', full_refresh: bool = False) -> str:
            """Run and test models. This writes to the warehouse. Narrow it with `select`."""
            args = ['build'] + (['--select', select] if select else [])
            if full_refresh:
                args.append('--full-refresh')
            return _dbt(project, *args)

        def dbt_test(select: str = '') -> str:
            """Run tests only, without rebuilding models."""
            args = ['test'] + (['--select', select] if select else [])
            return _dbt(project, *args)

        def dbt_seed() -> str:
            """Load the project's seed CSVs into the warehouse."""
            return _dbt(project, 'seed')

        def query_sql(sql: str, limit: int = 50) -> str:
            """Run a read-only SQL query against the project's DuckDB warehouse."""
            return _query_duckdb(project, sql, limit)

        return [
            Capability(
                id='dbt',
                description='Build, test, and inspect a dbt project.',
                tools=[dbt_ls, dbt_parse, dbt_compile, dbt_build, dbt_test, dbt_seed, query_sql],
            )
        ]

    def verify(self, spec: TaskSpec) -> Verification:
        """The project must parse and its models must build and pass their tests."""
        project = spec.resolved_workspace
        if spec.kind == 'analysis':
            output = _dbt(project, 'parse')
            passed = output.startswith('exit=0')
            return Verification(passed=passed, detail=_tail(output))
        output = _dbt(project, 'build')
        passed = output.startswith('exit=0')
        return Verification(passed=passed, detail=_tail(output))


def _tail(output: str, lines: int = 12) -> str:
    return '\n'.join(output.strip().splitlines()[-lines:])


def _query_duckdb(project: Path, sql: str, limit: int) -> str:
    """Open the project's DuckDB file read-only and return rows as text."""
    forbidden = {'insert', 'update', 'delete', 'drop', 'alter', 'create', 'attach', 'copy'}
    if set(sql.lower().split()) & forbidden:
        return 'refused: query_sql is read-only; use dbt_build to change the warehouse'
    try:
        import duckdb
    except ImportError:
        return 'duckdb is not installed in this environment'

    candidates = sorted(project.glob('*.duckdb')) + sorted(project.glob('**/*.duckdb'))
    if not candidates:
        return 'no DuckDB database found yet; run dbt_build first'
    try:
        connection = duckdb.connect(str(candidates[0]), read_only=True)
        rows = connection.sql(f'SELECT * FROM ({sql}) LIMIT {int(limit)}')
        return _clip(str(rows))
    except Exception as exc:  # noqa: BLE001 - surfaced to the model as a tool result
        return f'query failed: {type(exc).__name__}: {exc}'
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass


register(DbtPack())
