---
name: debugging-failures
description: Diagnosing a dbt build or test failure. Use when dbt reports a compilation error, a database error, a failing test, a ref that cannot be resolved, or output that does not match the model source.
---

dbt errors name their cause more precisely than they first appear. Read the error, find the compiled SQL, and reproduce the failure before changing anything.

## Read the compiled SQL

The model source is Jinja; the warehouse ran SQL. A database error refers to the SQL, so read that:

```
target/compiled/<project>/models/<path>/<model>.sql
```

`dbt_compile(select='<model>')` refreshes it without touching the warehouse. Column names and CTE structure in the compiled file are what the error message is talking about; a `ref` that resolved to an unexpected relation is visible there and nowhere else.

For a failing test, the compiled test SQL sits under `target/compiled/<project>/models/.../<test_name>.sql`. Running it through `query_sql` returns the offending rows — that result set is the defect, and reading it is faster than reasoning about what the test means.

Done when you have seen the failure yourself, either as the compiled SQL erroring or as the rows the test returned.

## Error signatures

- **`Compilation Error ... depends on a node named X which was not found`** — a `ref` or `source` naming something that does not exist. Check the spelling against `dbt_ls`, and check the source is declared in a yml.
- **`Compilation Error ... Found a cycle`** — two models `ref` each other. The fix is a layering change, not a SQL change.
- **`Database Error ... column "x" does not exist`** — read the compiled SQL; the column is usually renamed in an upstream model or aliased in a CTE above where it is used.
- **`Database Error ... ambiguous`** — two joined relations expose the same column. Qualify it.
- **`Runtime Error ... Could not find profile`** — a project configuration problem, not a model problem.
- **Test failure with a row count** — the count is how many rows violate the assertion. Run the compiled test SQL to see which.

## Narrow the blast radius

Selectors make the loop tight, and a tight loop is what makes the cause obvious:

- `dbt_build(select='my_model')` — the one model.
- `dbt_build(select='my_model+')` — it and everything downstream, to confirm a fix did not break a child.
- `dbt_build(select='+my_model')` — it and everything upstream, when the cause is above it.
- `dbt_test(select='my_model')` — tests only, no rebuild.

Start at the narrowest selector that reproduces the failure and widen only to confirm the fix.

## Fix the cause

The failing test is the messenger. Correct the SQL or the data so the assertion holds on its own terms, then re-run the same selector that failed and confirm it is green.

When a test turns out to encode the wrong rule, change the assertion and say in the commit why the old rule was wrong — that is a deliberate decision, distinct from loosening a test to make red go away.
