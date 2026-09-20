---
name: grain-and-joins
description: Grain of a model and what joins do to it. Use when a model returns duplicate rows, a count or sum is inflated, a uniqueness test fails, or a join fans out.
---

The **grain** is what one row means: "one row per order", "one row per customer per day". Every model has one. Most wrong numbers in a warehouse are a grain that changed without anyone noticing, because a join fanned out and inflated the aggregates downstream.

## Establish the grain first

Before touching the SQL, write the grain as a sentence. Then prove it against the data:

```sql
select <key columns>, count(*)
from <model>
group by all
having count(*) > 1
```

Empty result means the grain holds. Rows back means it does not, and that result set is the bug — read it before changing anything.

Done when you can state the grain in one sentence and the check returns no rows.

## Fanout

A join fans out when the right side has more than one row per join key. The left side's rows multiply, and every downstream `sum` and `count` silently inflates.

Find the culprit by checking each joined relation on its own join key with the duplicate query above. The one that returns rows is the one fanning out.

Three ways to keep the grain, in order of preference:

1. **Aggregate before joining.** Roll the right side up to the left side's grain in a CTE, then join one-to-one. This is almost always the right answer.
2. **Deduplicate deliberately** with `qualify row_number() over (partition by <key> order by <tiebreak>) = 1`. State the tiebreak in a comment: picking a row is a business decision, not a technical one.
3. **Accept the finer grain** and rename the model to say so. A model that is now one row per order-line should be named for order-lines and tested on that key.

## Aggregates that survive a fanout

When a fanout is genuinely unavoidable, `count(distinct ...)` and `sum` over a pre-aggregated CTE still give the right answer where bare `count(*)` and `sum` do not. Prefer restoring the grain anyway; reach for this only when the join really must stay.

## Lock it in

A grain you proved once is a grain that drifts next week. Encode it as a test so the next change fails loudly:

- Single-column grain: `unique` and `not_null` on that column.
- Composite grain: `dbt_utils.unique_combination_of_columns` with the full key list.

Adding that test is part of fixing the bug, not a follow-up.
