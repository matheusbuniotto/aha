---
name: incremental-models
description: Incremental materialization for dbt models. Use when a table is slow or expensive to rebuild, when choosing a unique_key or incremental_strategy, when late-arriving or updated rows are missed, or when an incremental model has drifted from a full refresh.
---

Incremental models trade correctness guarantees for build time. A table materialization is recomputed from scratch every run and is therefore always right; an incremental one processes new rows and accumulates whatever the filter missed. Reach for incremental when a full refresh is genuinely too slow or too expensive, and keep the table materialization while it is affordable.

## Shape

```sql
{{ config(
    materialized='incremental',
    unique_key='order_id',
    incremental_strategy='merge',
    on_schema_change='append_new_columns'
) }}

select * from {{ ref('stg_orders') }}

{% if is_incremental() %}
  where updated_at >= (select coalesce(max(updated_at), '1900-01-01') from {{ this }})
{% endif %}
```

Four decisions live in that block:

- **`unique_key`** — the grain. With it, matching rows are updated; without it, every processed row is appended and re-runs duplicate.
- **`incremental_strategy`** — `merge` updates existing rows and is the default choice. `append` is for immutable event streams where a row is never revised. `delete+insert` suits partition rebuilds.
- **The `is_incremental()` filter** — what counts as new. This is where correctness is won or lost.
- **`on_schema_change`** — `append_new_columns` keeps a new column from silently vanishing on the next run.

## The lookback window

`max(updated_at) from {{ this }}` misses any row that arrived late or was revised after the last run. A source that can be corrected retroactively needs a window wide enough to catch the revision:

```sql
where updated_at >= (
    select coalesce(max(updated_at), '1900-01-01') from {{ this }}
) - interval '3 days'
```

Size the window to how late the source actually corrects itself, and say so in a comment. With a `unique_key` and `merge`, reprocessing those days is idempotent — the overlap costs a little compute and buys correctness.

Filter on the column that tracks *revision* (`updated_at`), not the one that tracks the event (`order_date`). An order created last month and refunded today has an old `order_date` and a new `updated_at`.

## Verify against a full refresh

An incremental model is correct when it matches the table it replaces. Prove it once:

```
dbt_build(select='my_model', full_refresh=True)
```

then compare row count and the key aggregates against the incremental result. Drift means the filter is missing rows — fix the filter rather than widening the test.

`--full-refresh` is also the repair when a model has already drifted: it rebuilds from scratch and resets the accumulated error.
