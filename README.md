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
triage ─▶ branch ─▶ explore ─▶ agent works ─▶ verify ─▶ (optional) PR
```

1. **triage** — what kind of task, how big, how destructive, how clear? Sets the instructions and tightens the policy. Cheap, and no frontier model. See [Triage](#triage-were-trialling-jev).
2. **branch** — switches to `aha/TASK-12`. One ticket, one diff, easy to throw away.
3. **explore** — reads dbt's manifest and works out which tables you probably mean, and what feeds them. So the model doesn't invent a `ref()`.
4. **work** — the agent edits files and runs dbt. Risky calls stop and ask you.
5. **verify** — we run `dbt build` ourselves. The model's opinion of its own work is ignored.
6. **PR** — with `--pr`, verified work gets pushed and a pull request opened.

Everything lands in a SQLite journal: `uv run aha runs`.

## Triage (we're trialling Jev)

Four questions get answered before the expensive model is woken up, because each
one changes what it's allowed to do. [TypeSafe's Jev](https://docs.typesafe.ai)
answers all four in a single typed call:

```bash
uv sync --group jev        # + TYPESAFE_API_KEY in .env
```

| judgment | question | what it changes |
|---|---|---|
| **kind** | which task is this? | instructions + default risk posture |
| **size** | how much work? | small tasks get a shorter step budget |
| **caution** | how bad if it's wrong? | destructive work gates file edits too |
| **clarity** | specific enough to act on? | a warning, and an opt-in refusal gate |

```bash
$ uv run aha classify "drop and rebuild every mart with a full refresh"
model_build (0.99 via jev) · size 0.97 · caution 0.90 · clarity 0.22
  · looks destructive (0.90): every file edit now needs approval too
  · looks vague (0.22): expect to be asked what you meant

$ uv run aha classify "add a not_null test to stg_orders.order_id"
test_coverage (1.00 via jev) · size 0.01 · caution 0.03 · clarity 0.68
  · looks small (0.01): step budget cut to 20
```

Read clarity as *"how much back and forth should I expect"*, not as a grade.
Jev is asked whether an engineer could act without asking a question first,
which is a high bar: `"build a daily revenue mart from stg_orders and
stg_customers"` — a perfectly good ticket — scores **0.16**. So nothing is
refused on clarity unless you ask for it:

```bash
uv run aha run "..." -a autonomous --min-clarity 0.3   # off by default
```

**Every adjustment is one-directional.** Jev can narrow the blast radius or
shorten the leash; it can never widen either. A destructive-looking request
raises `mutate` tools to `high` — reusing the gate you already have instead of
inventing a second one — and reads are never escalated. A big task doesn't earn
a bigger budget: ceilings are yours to grant.

No key, no SDK, or a failed call? A keyword rule answers `kind` and the other
three stay unknown, where **unknown means no adjustment**. A missing classifier
can't tighten or loosen anything. `uv run aha doctor` says which one you've got.

Worth knowing: the keyword rules get short dbt requests right about as often,
but their confidence is an artifact of how many words matched. Jev's is
calibrated — which is what makes it safe to wire into policy at all.

## Commands

## Commands

## Commands

```bash
uv run aha run "..." -w path [-t TASK-12] [-a supervised|guarded|autonomous] [--pr]
uv run aha explore "why is revenue wrong?"   # tables + lineage it would start from
uv run aha classify "why is revenue wrong?"  # how it'd route + judge the request
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
