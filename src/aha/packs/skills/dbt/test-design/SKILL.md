---
name: test-design
description: Choosing dbt tests that catch real defects. Use when adding tests to a model, when a test is noisy or always passes, when deciding between a generic and a singular test, or when setting severity.
---

A test earns its place by failing when something is genuinely wrong and staying quiet otherwise. A suite of tests that cannot fail costs build time and buys nothing.

## The floor every model meets

1. **Grain** — `unique` and `not_null` on the key. Composite keys use `dbt_utils.unique_combination_of_columns`.
2. **Every `ref` join key** — `relationships` to the parent model. This is what catches orphaned rows after an upstream change.
3. **Every low-cardinality string the business reads** — `accepted_values`. This catches a new status code the day it appears rather than the day a dashboard looks wrong.

Done when each of the three is either present or has a written reason it does not apply.

## Generic before singular

Generic tests (`unique`, `not_null`, `relationships`, `accepted_values`, and the `dbt_utils` set) are declarative, reusable, and read in the yml next to the column they govern. Reach for them first.

Write a **singular** test — a `.sql` file in `tests/` returning offending rows — when the assertion spans models or encodes a business rule: revenue reconciling to a source total, no order dated before its customer signed up, a daily table with no gaps. Name the file for the rule it enforces, so a failure names itself.

## Severity

Default severity is `error`, which stops the build. That is right for anything that makes downstream numbers wrong.

Use `severity: warn` for conditions that are real but not stop-the-line — a slowly growing null rate, a rare unmapped category. Pair it with `error_if` / `warn_if` thresholds so it escalates on its own:

```yaml
tests:
  - not_null:
      config:
        severity: warn
        error_if: '>1000'
```

A `warn` that nobody will ever act on is better deleted than left to train people to ignore output.

## A test that cannot fail

Before adding a test, know what would make it fail. If nothing could, it is load without cover.

The sharp check: break the model on purpose — drop a filter, remove a `distinct` — and confirm the test goes red. A test that stays green against a deliberately broken model is measuring nothing. Revert the break once you have seen it fail.

This is also how you fix a failing test honestly: make the data or the SQL correct so the test passes on its own terms. Loosening the assertion to accommodate bad data converts a real defect into a silent one.
