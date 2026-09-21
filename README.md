# aha

**Agentic Harness for Analytics.** Run dbt and analytics tasks with an agent,
without handing it the keys.

You describe the task in plain English. It works on its own branch, asks before
anything risky, and then **code** — not the model — checks whether the work
actually landed. `dbt build` green or it didn't happen.

```bash
uv sync --group dbt
cp .env.example .env          # put a key in it
uv run aha run "add a staging model for raw orders with tests" -w ./my-dbt-project -t TASK-12
```

That's it. The rest of this file is detail you can read when you need it.

## What a run does

```
classify ─▶ branch ─▶ explore ─▶ agent works ─▶ verify ─▶ (optional) PR
```

1. **classify** — what kind of task is this? Decides instructions and risk posture. Free, offline, no model call.
2. **branch** — switches to `aha/TASK-12`. One ticket, one diff, easy to throw away.
3. **explore** — reads dbt's manifest and works out which tables you probably mean, and what feeds them. So the model doesn't invent a `ref()`.
4. **work** — the agent edits files and runs dbt. Risky calls stop and ask you.
5. **verify** — we run `dbt build` ourselves. The model's opinion of its own work is ignored.
6. **PR** — with `--pr`, verified work gets pushed and a pull request opened.

Everything lands in a SQLite journal: `uv run aha runs`.

## Commands

```bash
uv run aha run "..." -w path [-t TASK-12] [-a supervised|guarded|autonomous] [--pr]
uv run aha explore "why is revenue wrong?"   # tables + lineage it would start from
uv run aha classify "why is revenue wrong?"  # how it'd route the request
uv run aha packs                             # what it knows how to be asked
uv run aha doctor                            # which models/keys are wired up
uv run aha runs                              # history
```

Or just `make` — it lists everything, and `make demo` runs the whole thing on a
throwaway copy of the example project.

## Not letting it break prod

Safety is code, not prompt wishes:

- Filesystem is rooted at your workspace. `.env`, `profiles.yml`, `*.pem`, `.git` are unreadable, full stop.
- **No shell** in the dbt pack. dbt runs as a fixed argv (`dbt build --select x`), never a command line the model wrote.
- `query_sql` is read-only: writes are refused before a connection is even opened, and every query gets a `LIMIT`.
- Every tool is `read`, `mutate`, or `high`. Raising autonomy narrows *what* gets gated — it never ungates a high-risk call.

| autonomy | gated | who answers |
|---|---|---|
| `supervised` | mutate + high | you, at the terminal |
| `guarded` | high | you, at the terminal |
| `autonomous` | high | a pre-authorised list — denies by default |

Unattended means naming the calls up front (`--allow dbt_build`), not turning
the gate off. Plus hard per-run limits on cost and steps.

## Watching it work

You get live output, not a black box:

```
    task TASK-12-2377134c · documentation (0.95 via rules)
  branch aha/TASK-12 (created from main)
 explore seeds (2): raw_customers, raw_orders ...
0:05 -> read_file path=models/staging/schema.yml
0:08 <- find_tables: raw_customers (seed) seeds/raw_customers.csv [+7 lines]
0:08 .. Grain is one row per customer. Now let me count.
```

Same event stream the journal is built from, so what you watch and what's
recorded can't drift. `--quiet` if you'd rather not.

## Warehouses

`query_sql` follows your `profiles.yml` — no second config to keep in sync.

| profile type | needs |
|---|---|
| `duckdb` | nothing, it's local |
| `databricks` | `uv sync --group databricks`, and creds in your profile (`env_var` references work) |

Use a read-only service principal on Databricks anyway. Our guard is a guard,
not a permission system. Other adapters say plainly that they're unsupported
rather than failing weirdly — adding one is a ~20 line class in `warehouse.py`.

## Models

Anything Pydantic AI knows (`anthropic:...`, `openai:...`, `bedrock:...`), plus
two prefixes for OpenAI-compatible endpoints:

| name | endpoint | key |
|---|---|---|
| `opencode-go/<id>` | opencode Zen | `OPENCODE_API_KEY` |
| `openai-compatible/<id>` | `$AHA_OPENAI_BASE_URL` | `AHA_OPENAI_API_KEY` |

[opencode Go](https://opencode.ai/docs/go/) is flat-rate, which is a cheap way
to run the boring tail of tasks. Note it doesn't report per-token cost, so
`--max-usd` doesn't bind there; `max_steps` is the ceiling that does.

Set `AHA_MODEL` in `.env` and forget about it. `uv run aha doctor` tells you
what's actually configured.

## Adding your own domain

A "pack" is a domain plugged into the fabric. dbt is one; `generic` is the
minimal example. Implement six methods:

```python
class Pack(Protocol):
    def kinds(self) -> tuple[TaskKind, ...]: ...  # what it can be asked for
    def policy(self) -> Policy: ...  # blast radius
    def instructions(self, spec) -> str: ...  # house style
    def capabilities(self, spec) -> list[...]: ...  # tools
    def explore(self, spec) -> str: ...  # pre-run survey
    def verify(self, spec) -> Verification: ...  # did it actually work
```

Nothing in `aha.fabric` knows what dbt is. Team conventions can go in
`SKILL.md` files (`context={'skills_dir': '.agents/skills'}`) with no Python at
all — the dbt pack ships four covering grain, tests, incremental models, and
debugging.

## Tests

```bash
make test-unit   # fast, no dbt
make test        # everything, including real dbt runs
```

End-to-end tests drive a scripted model, so they're deterministic and need no
API key — but policy, approvals, journalling, git, dbt, and verification all run
for real.

## Where this is going

Local terminal runs today. `Runner` is a protocol, so a cloud runner changes
where the process runs and who approves — not what a task is or how it's judged.
Step persistence and the audit trail are already in place for that.

Built on [Pydantic AI](https://pydantic.dev/docs/ai/). Idea borrowed from
[machinist](https://github.com/owainlewis/machinist): named tasks, one
entrypoint, a durable record, nothing auto-ships.

MIT.
