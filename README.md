# aha

An agent fabric for data and analytics engineering work.

A team describes a task in plain language, watches the agent do it with a human
approving anything risky, and gets an independent verdict on whether the work
actually landed. Once a class of task has proven itself, the same task runs
unattended -- with the risky calls named in advance rather than the safety gate
switched off.

Built on [Pydantic AI](https://pydantic.dev/docs/ai/) and its harness capability
library. Inspired by [machinist](https://github.com/owainlewis/machinist)'s core
idea -- named tasks, a single controlled entrypoint, a durable record, no
auto-shipping -- reworked around dbt modelling and analysis.

## Install

```bash
uv sync --group dbt
```

## Use

```bash
uv run aha packs                              # what the agent knows how to be asked for
uv run aha classify "why is revenue wrong?"   # how a request would be routed, for free
uv run aha run "add a staging model for raw orders with tests" \
    --workspace examples/jaffle --pack dbt --autonomy supervised
uv run aha runs                               # the audit trail
```

Set `ANTHROPIC_API_KEY` to run against a real model. The test suite runs offline
against a scripted model, so the whole path is exercised without a provider.

## How it fits together

```
TaskSpec ──▶ classify ──▶ Pack ──▶ build_agent ──▶ Runner ──▶ verify
   │                       │           │                        │
 policy                tools +     + filesystem, shell,     independent
 autonomy            house style     approval gate,         check, in code
                                     compaction, steps
```

### The fabric (`aha.fabric`)

Domain-free. A `TaskSpec` is a goal, a workspace, an autonomy level, and a
`Policy`. A `Runner` takes one and returns a `RunOutcome`. Nothing here knows
what dbt is.

### Packs (`aha.packs`)

A pack is a domain plugged into the fabric. It supplies the task kinds it answers
to, its default blast radius, its house style, its tools, an optional skill
library, and an independent check that the work is done. `dbt` is the first;
`generic` is the smallest worked example.

```python
class Pack(Protocol):
    name: str
    def kinds(self) -> tuple[TaskKind, ...]: ...
    def policy(self) -> Policy: ...
    def instructions(self, spec: TaskSpec) -> str: ...
    def capabilities(self, spec: TaskSpec) -> list[AgentCapability[None]]: ...
    def skills(self) -> Path | None: ...
    def verify(self, spec: TaskSpec) -> Verification: ...
```

A pack's instructions ride on the capability that owns its tools, so guidance
and the tools it governs travel together and the fabric never has to know what
either says.

### Skills

Deep dbt know-how lives in portable [Agent Skill](https://pydantic.dev/docs/ai/harness/skills/)
packages under `src/aha/packs/skills/dbt/`, loaded on demand rather than held in
context every run:

| Skill | Fires when |
|---|---|
| `grain-and-joins` | duplicate rows, inflated aggregates, a fanning join |
| `test-design` | adding tests, a noisy test, generic vs singular, severity |
| `incremental-models` | a slow table, `unique_key` choice, late-arriving rows |
| `debugging-failures` | a compilation error, a database error, a failing test |

Two selection mechanisms work together. Classification picks the *primary*
instruction and the risk posture deterministically before the run starts; skills
cover the long tail, and the model pulls one in when a task turns out to need it.

A team adds its own house conventions by dropping a `SKILL.md` into a directory
and naming it, without writing Python:

```python
TaskSpec(..., context={'skills_dir': '.agents/skills'})
```

## Safe execution

Safety is enforced in code before a tool runs. None of it is delegated to the
system prompt.

- **Workspace confinement.** The filesystem is rooted at the workspace.
  Credentials, `profiles.yml`, `.env`, and `.git` are unreadable whatever the
  model asks for.
- **No free shell.** dbt is invoked with a fixed argument vector, never through
  a shell. The agent picks verbs and selectors, not command lines. Any remaining
  shell access is an explicit executable allowlist.
- **Read-only analysis.** `query_sql` opens DuckDB read-only and refuses
  statements that would write.
- **Risk bands.** Every tool is `read`, `mutate`, or `high`. Reads are free,
  mutations are recoverable, `high` touches the warehouse or the network.
- **The gate does not open.** Raising autonomy narrows *what* is gated; it never
  ungates a high-risk call. What changes is who answers:

  | Autonomy | Gated | Answered by |
  |---|---|---|
  | `supervised` | mutate + high | a person at the terminal |
  | `guarded` | high | a person at the terminal |
  | `autonomous` | high | a pre-authorisation rule -- `DenyUnattended` by default |

  Delegating a task to the cloud means writing down which risky tools are
  allowed (`PreAuthorized.of('dbt_build')`), not removing the check.
- **Hard ceilings.** Per-run cost and request limits are enforced by the runtime.
- **Independent verification.** The agent's own claim of success is ignored.
  `Pack.verify` runs real code -- for dbt, `dbt build` -- and that verdict decides
  `RunOutcome.ok`. There is a test asserting a confident lie still fails.
- **A durable record.** Every classification, approval, refusal, and verdict
  lands in SQLite and is queryable with `aha runs`. A `Recorder` capability
  listens to the run's event stream as well, so the trail covers tool calls the
  approval gate was never asked to rule on -- reads, planning, compaction -- and
  not just the ones it gated.

## Classification

Which kind of task this is decides the instructions and the risk posture, so it
runs before any frontier model does. TypeSafe's Jev answers it as a typed
judgment when `TYPESAFE_API_KEY` is set; otherwise a hint-based rule answers it
offline. Both report a confidence, and neither can break a run -- a failed
classifier falls through to the rules.

## Toward AWS

The `Runner` protocol is the seam. A task is defined, gated, and judged
identically wherever it executes; a cloud runner changes where the process runs
and who approves, not what a task is.

Already in place: `StepPersistence` snapshots every step to SQLite, so a run can
be resumed or forked; `PreAuthorized` expresses unattended permission; the
journal is the audit trail a supervisor reads afterwards.

Still to build: a queue-driven `RemoteRunner`, a container image, and moving the
journal and step store off local SQLite.

## Tests

```bash
uv run --group dbt --group dev pytest
```

The end-to-end tests drive a scripted model, so they are deterministic and need
no API key -- but policy, approval, journalling, dbt, and verification all run
for real.
