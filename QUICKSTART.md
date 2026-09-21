# Quickstart

Five minutes, from nothing to a verified agent run. Nothing here touches a repo
you care about.

## 1. Install

```bash
git clone https://github.com/matheusbuniotto/aha && cd aha
uv sync --group dbt
cp .env.example .env
```

Put one model key in `.env` and set `AHA_MODEL` to match:

```bash
AHA_MODEL=anthropic:claude-sonnet-4-6      # or openai:gpt-5, or opencode-go/<id>
ANTHROPIC_API_KEY=sk-...
```

Everything else in that file is optional. Check what landed:

```bash
uv run aha doctor
```

## 2. Run the demo

```bash
make demo
```

That copies the bundled `examples/jaffle` project to a scratch directory, runs
it unattended, and prints what happens as it happens. Then look at the result:

```bash
make diff     # what it changed, on its own branch
make runs     # the run, its verdict, what it cost
```

`make demo` takes overrides:

```bash
make demo GOAL="add accepted_values tests to the status column" TASK=TASK-7
make demo-supervised     # same thing, but you approve each risky call
```

## 3. Point it at your own project

```bash
uv run aha run "add a staging model for raw payments with tests" \
  --workspace ~/code/our-dbt-project \
  --task-id TASK-12
```

Defaults worth knowing:

- **Supervised.** Every file edit and every warehouse write stops and asks you. Press `y` or `n`.
- **On a branch.** `aha/TASK-12`, cut from wherever you were. Your working tree is never edited in place on `main`.
- **Verified by dbt, not by the model.** The run only counts as OK if `dbt build` passes afterwards.

Nothing is committed and nothing is pushed unless you add `--pr`.

## 4. Let it off the leash (a bit)

Once a kind of task has proven itself, run it unattended. You have to name the
risky tools up front — there is no "just allow everything":

```bash
uv run aha run "add not_null tests to every staging key" \
  -w ~/code/our-dbt-project -t TASK-13 \
  --autonomy autonomous --allow dbt_build --pr
```

Anything risky that you did *not* name gets refused, and the refusal is in the
journal. `--pr` opens a pull request if, and only if, the run verified.

## Gotchas

**`Could not find profile`** — aha runs dbt with `--profiles-dir` set to the
project directory, so `~/.dbt/profiles.yml` is ignored on purpose (the agent
can't read your home directory). Put a `profiles.yml` in the project, pointed at
a dev target.

**It won't run shell commands.** Correct: the dbt pack has no shell at all. It
gets dbt verbs, SQL, file editing, and lineage lookups. If you need `git`, use
the `generic` pack.

**Classification says `via rules`.** Jev is optional. Without
`TYPESAFE_API_KEY`, a keyword rule routes the request and everything else runs
identically. `uv run aha doctor` tells you which one answered.

**It stopped after 20 steps.** A request judged small gets a shorter budget.
Say more about what you want, or raise `max_steps` in the pack's policy.

**Cost shows `$0.0000`.** opencode Go is flat-rate and reports no per-token
cost, so `--max-usd` can't bind there. Step count is the real ceiling.

## Next

- `make` — every command, with a one-line description
- [README](README.md) — how it works, and how to plug in your own domain
