"""The terminal runner: the first place a Data and AI team meets the fabric."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from . import packs  # noqa: F401 - registers the built-in packs
from .fabric import (
    Autonomy,
    ConsoleReporter,
    LocalRunner,
    NullReporter,
    Policy,
    PreAuthorized,
    TaskSpec,
    get,
    names,
)
from .fabric.classify import classify
from .fabric.models import ENDPOINTS, describe
from .fabric.runner import DEFAULT_JOURNAL

load_dotenv()

DEFAULT_MODEL = os.getenv('AHA_MODEL') or 'anthropic:claude-sonnet-4-6'

app = typer.Typer(
    add_completion=False,
    help='aha -- Agentic Harness for Analytics. Run supervised agent tasks over a workspace.',
)
console = Console()


@app.command()
def run(
    goal: Annotated[str, typer.Argument(help='What you want done, in plain language.')],
    workspace: Annotated[Path, typer.Option('--workspace', '-w', help='Directory the agent is confined to.')] = Path(
        '.'
    ),
    pack: Annotated[str, typer.Option('--pack', '-p', help='Domain pack to use.')] = 'dbt',
    autonomy: Annotated[
        Autonomy, typer.Option('--autonomy', '-a', help='How much runs without a human.')
    ] = Autonomy.supervised,
    model: Annotated[
        str,
        typer.Option('--model', '-m', help='Provider model, or opencode-go/<id>, or openai-compatible/<id>.'),
    ] = DEFAULT_MODEL,
    name: Annotated[str | None, typer.Option('--name', help='Slug recorded in the journal.')] = None,
    task_id: Annotated[
        str | None,
        typer.Option('--task-id', '-t', help='Ticket this run belongs to, such as TASK-12. Names the branch.'),
    ] = None,
    branch: Annotated[bool, typer.Option('--branch/--no-branch', help='Isolate the run on its own git branch.')] = True,
    pr: Annotated[bool, typer.Option('--pr', help='Open a pull request when the run verifies.')] = False,
    quiet: Annotated[bool, typer.Option('--quiet', '-q', help='Only print the final report.')] = False,
    journal: Annotated[Path, typer.Option('--journal', help='Where the audit trail is kept.')] = DEFAULT_JOURNAL,
    max_usd: Annotated[float, typer.Option('--max-usd', help='Hard spend ceiling for this run.')] = 2.0,
    allow: Annotated[
        list[str] | None,
        typer.Option('--allow', help='Pre-authorise a gated tool, repeatable. Needed to run unattended.'),
    ] = None,
) -> None:
    """Run one task end to end and report whether it actually landed."""
    domain = get(pack)
    spec = TaskSpec(
        name=name or (task_id.lower() if task_id else 'task'),
        goal=goal,
        workspace=workspace,
        pack=pack,
        task_id=task_id,
        branch=branch,
        pull_request=pr,
        autonomy=autonomy,
        model=model,
        policy=_with_ceiling(domain.policy(), max_usd),
    )

    console.print(f'[bold]{spec.name}[/] · pack [cyan]{pack}[/] · autonomy [yellow]{autonomy}[/]')
    console.print(f'workspace [dim]{spec.resolved_workspace}[/]\n')

    approver = PreAuthorized.of(*allow) if allow else None
    if allow:
        console.print(f'pre-authorised [magenta]{", ".join(allow)}[/]\n')

    reporter = NullReporter() if quiet else ConsoleReporter(console=console)
    runner = LocalRunner(approver=approver, journal_path=journal, reporter=reporter)
    outcome = asyncio.run(runner.run(spec))
    _report(outcome)
    raise typer.Exit(0 if outcome.ok else 1)


@app.command('packs')
def list_packs() -> None:
    """Show the registered domain packs and the tasks each one understands."""
    for pack_name in names():
        domain = get(pack_name)
        console.print(f'\n[bold cyan]{pack_name}[/]')
        for kind in domain.kinds():
            console.print(f'  [green]{kind.name:16}[/] {kind.description}')


@app.command('classify')
def classify_goal(
    goal: str,
    pack: Annotated[str, typer.Option('--pack', '-p')] = 'dbt',
) -> None:
    """Show how a request would be classified, without running anything."""
    verdict = classify(goal, get(pack).kinds())
    flag = ' [yellow](uncertain)[/]' if verdict.uncertain else ''
    console.print(f'{verdict}{flag}')


@app.command('explore')
def explore(
    goal: Annotated[str, typer.Argument(help='What you want done, in plain language.')],
    workspace: Annotated[Path, typer.Option('--workspace', '-w')] = Path('.'),
    pack: Annotated[str, typer.Option('--pack', '-p')] = 'dbt',
) -> None:
    """Show the survey a run would start from: table candidates and their lineage."""
    spec = TaskSpec(name='explore', goal=goal, workspace=workspace, pack=pack)
    brief = get(pack).explore(spec).strip()
    console.print(brief or f'[dim]the {pack} pack has nothing to survey here[/]')


@app.command('doctor')
def doctor(
    model: Annotated[str, typer.Option('--model', '-m')] = DEFAULT_MODEL,
) -> None:
    """Show how a model name routes and which provider credentials are present."""
    console.print(f'[bold]model[/]  {describe(model)}\n')

    table = Table(box=None, pad_edge=False, title='OpenAI-compatible endpoints', title_justify='left')
    for column in ('prefix', 'base url', 'api key env', 'status'):
        table.add_column(column)
    for endpoint in ENDPOINTS:
        base = endpoint.base_url or f'${endpoint.base_url_env}'
        ready = bool(os.getenv(endpoint.api_key_env))
        if endpoint.base_url is None:
            ready = ready and bool(os.getenv(endpoint.base_url_env or ''))
        status = '[green]ready[/]' if ready else '[yellow]not configured[/]'
        table.add_row(f'{endpoint.prefix}*', base, endpoint.api_key_env, status)
    console.print(table)

    console.print('\n[dim]example[/] aha run "..." -m opencode-go/kimi-k3')


@app.command('runs')
def list_runs(
    journal: Annotated[Path, typer.Option('--journal')] = DEFAULT_JOURNAL,
    limit: Annotated[int, typer.Option('--limit', '-n')] = 20,
) -> None:
    """List recent runs from the audit trail."""
    if not journal.exists():
        console.print(f'[dim]no journal at {journal}[/]')
        return
    with closing(sqlite3.connect(journal)) as db:
        rows = db.execute(
            'SELECT id, kind, branch, autonomy, status, usd, started_at FROM runs ORDER BY started_at DESC LIMIT ?',
            (limit,),
        ).fetchall()

    table = Table(box=None, pad_edge=False)
    for column in ('run', 'kind', 'branch', 'autonomy', 'status', 'usd', 'started'):
        table.add_column(column)
    for run_id, kind, branch, autonomy, status, usd, started in rows:
        colour = 'green' if status == 'succeeded' else 'red'
        table.add_row(run_id, kind, branch or '-', autonomy, f'[{colour}]{status}[/]', f'{usd:.4f}', started)
    console.print(table)


def _with_ceiling(policy: Policy, max_usd: float) -> Policy:
    return replace(policy, max_usd=max_usd)


def _report(outcome) -> None:
    console.print(f'\n[dim]run[/] {outcome.run_id}')
    console.print(f'[dim]kind[/] {outcome.classification}')
    console.print(f'[dim]status[/] {outcome.status} · [dim]cost[/] ${outcome.usd:.4f}')
    if outcome.branch:
        console.print(f'[dim]branch[/] {outcome.branch}')
    if outcome.pull_request:
        console.print(f'[dim]pr[/] {outcome.pull_request.url or outcome.pull_request.detail}')

    mark = '[green]verified[/]' if outcome.verification.passed else '[red]not verified[/]'
    console.print(f'[dim]check[/] {mark}')
    if outcome.verification.detail:
        console.print(f'[dim]{outcome.verification.detail}[/]')
    if outcome.output:
        console.print(f'\n{outcome.output}')


if __name__ == '__main__':
    app()
