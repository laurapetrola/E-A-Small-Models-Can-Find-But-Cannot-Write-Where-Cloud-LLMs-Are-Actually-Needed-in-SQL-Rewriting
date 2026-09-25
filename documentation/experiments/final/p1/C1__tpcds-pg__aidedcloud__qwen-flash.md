# C1 — `qwen` 8B **local FIND** · `flash` **WRITE** (2 agents)

**Started 23/08/2026** · **P1** regime: no plan · no masking · no schema-linking · no rules
· reasoning in natural mode (qwen = ON) · **n=5** · 21 TPC-DS/PostgreSQL queries · US$ 4.78

> **PARTIAL — the cell is running.** The tables below are regenerated from the store
> (`scripts/gen_cell_doc.py`); the reading below applies to what has already closed.

## Why this cell exists

It is **half of a pair**. Alone it answers nothing:

| arm | agents | FIND | WRITE |
|---|---|---|---|
| **C1** (this one) | 2 | **`qwen` 8B local** | `flash` |
| **C2** (ceiling) | 2 | **`flash`** | `flash` |

Same writer, same window, same queries — **the only variable is WHO FINDS**. If the ceiling ties with
the 8B, the finding **is not the wall**, and tiered allocation (small local finds, large writes)
is justified by **measurement**, not by intuition. It is the direct answer to P1's headline question.

And it completes the model ladder of the headline:

```
raw           1 agent, qwen does everything    → context
aided-local   qwen finds · local coder writes  → isolates the WRITER against C1
C1            qwen finds · flash writes        ← this cell
C2 (ceiling)  flash finds · flash writes       → isolates the FINDER against C1
C3            1 agent, flash does everything   → isolates DECOMPOSITION against C2
```

## This cell was REDONE FROM SCRATCH on 23/08

The first attempt (27 runs) was **discarded entirely**: it ran in a window where there were **orphan
queries** in PostgreSQL — leftovers of finished `run_matrix` runs whose backend kept executing, the
oldest at **27.7 hours**. They competed for CPU and I/O, and **execution time IS the measurement**.

| | contaminated window | outside it |
|---|---|---|
| runs | 38 | 2,747 |
| **timeouts** | **50%** | **5%** |
| measured gain | 23% | 60% |

Ten times more timeout. The data wasn't measuring capacity, it was measuring contention for the machine.
**Prevention installed:** `scripts/campanhas/kill_orphans.sh` runs before every cell across 8 chains,
and `stop_all.sh` is the only correct way to interrupt the campaign.

## The index HERE is MEASURED, not estimated

`INDEX_VALIDATION=executed`: each candidate is **actually created** inside a transaction,
timed (warm-up discarded + median of 3) and **undone via ROLLBACK**. There are **121 measured
recommendations** in the body, with before/after time recorded.

**And they show that the planner OVERESTIMATES** — example from `final_workload_pro_norules`:

> **This is NOT our finding (recorded 25/08).** Figure 1 of **LLMIA** (arXiv:2503.07884v2) is
> exactly this, and it is the **stated motivation** of their paper. Our number is **corroboration
> in a different regime**. Write it like this: *"Consistent with LLMIA, whose design is motivated by
> the gap between estimated cost and measured latency, we observe the same discrepancy…"*


| target | before | after | **measured** | estimated |
|---|---|---|---|---|
| `customer_demographics.cd_gender` | 5,172 ms | 5,133 ms | **0.8%** | **10.4%** |
| `date_dim.d_year` | 5,123 ms | 5,130 ms | **−0.1%** | 1.6% |
| `store_sales.ss_cdemo_sk` | 5,123 ms | 5,173 ms | **−1.0%** | 0.0% |

An index estimated at 10.4% gain **measures 0.8%**. It is the empirical justification for validating by
execution — and it answers the advisor's request to *make clear how the indexes were calculated*.

**Only applies to PostgreSQL.** The execution path exists only in `postgres.py`; in MySQL the
index is actually created but the benefit comes from `EXPLAIN FORMAT=JSON` — **cost, not time**.

## THE 40-MINUTE BUDGET PER RUN — declared parameter (25/08)

Each run gets **40 minutes**. Of **122 attempts**, **79 answered** within the budget and
**43 exceeded it**. **No query was left without data** — the minimum was **4 answered runs**.

**What overruns isn't the query in the database — it's the re-strategy loop.** The graph tries up to
**3 different strategies** (Loop 2) × **2 writer attempts** each, and each strategy redoes the local
architect's complete CoT, plus the S=1 gate measuring both sides with warm-up and median of 3, plus the
actual creation of the candidate indexes.

### The cost of the advice is NOT proportional to the cost of the query

| strategies tried | runs | median inference | maximum |
|---|---|---|---|
| 1 | 34 | **66 s** | 525 s |
| 2 | 8 | 135 s | 680 s |
| **3** | 37 | **692 s** | **6,156 s** |

A jump of **10×**, and none of it is database time. Per query it becomes evident:

| query | the **query** executes in | the **run** spends on inference |
|---|---|---|
| `q40` | **1.7 s** | 698 s |
| `q18` | **3.0 s** | 525 s |
| `q3` | **0.2 s** | 222 s |
| `q69` | **79.6 s** ← the slowest in the set | **135 s** ← among the cheapest |

`q69` is the slowest query to EXECUTE and one of the cheapest to OPTIMIZE, because it resolves on the
first strategy. The cost is proportional to **how many times FIND rethinks**, not to the weight of the
query.

- **Corrected vocabulary:** these queries are not *"heavy"*. They are **hard for the model**.
  `q40`, `q18`, and `q3` execute in less than 4 s and are exactly the ones that overrun the budget.
  Calling them heavy confuses database load with optimization difficulty — that was the misreading of
  24/08.

### And the overrun is SIGNAL, not noise — the correlation is perfect

| | queries | land |
|---|---|---|
| **0** overruns | `q1` `q69` `q7` `q9` `q30` `q81` | **5/5** |
| **≥4** overruns | `q11` `q51` `q38` `q40` `q67` `q18` | **0/5** |

When FIND gets it right the first time, the run costs 66 s and the query lands. When it needs all three,
it goes past 40 min and doesn't land. They are the same thing seen from two angles — which makes the
overrun a **measure of difficulty**, not an artifact of the harness.

### How to report

> *"Each run was given a 40-minute budget. Of 122 attempts, 79 answered within it and 43 exceeded it.
> No query was left without data — the minimum was 4 answered runs per query."*

- **It IS A PARAMETER, not a failure.** Same treatment as the three provisional ones
  (`q1`/`q30`/`q81`): measure the limit and declare it. And it preempts the overhead question with a
  measured number.
- **DO NOT raise the budget or re-run.** Touching it would break comparability with all cells
  already measured — a bigger risk than the gain. Decision of 25/08: **report**.

**Threat to validity (to declare):** when it overruns, the CLIENT gives up but the SERVER is not
notified and keeps working, competing with the next run — and execution time **is** the measurement.
The worst path was the execution-based index measurement, which ran 4 `EXPLAIN ANALYZE` **without**
`statement_timeout`; capped on 24/08 (`INDEX_MEASURE_TIMEOUT_S`, 180 s). The other paths already had a
ceiling. Residual damage small, but real.

**The `timeout` label in the store is misleading.** These records say *"baseline timeout"*, but the
durations fall between **2177 s and 2400 s** — it's the client giving up, not the original failing.
The `run_matrix` message was fixed on 24/08; older records keep the name for schema compatibility.

## What this cell does NOT authorize

- **Do not read it alone.** Without C2 there is no comparison; high or low reach here says nothing
  about the quality of the find.
- **P1's regime, not P2's.** No plan, no masking. A number from here is **never** cited as if it
  belonged to P2's architecture.

<!-- GENERATED BELOW — do not edit; regenerate with scripts/gen_cell_doc.py -->

# Cell `p1cloud_qwen_flash` — generated by `scripts/gen_cell_doc.py`

## `TPC-DS` · `POSTGRES` · arm **`aided-cloud`** · writer `cloud:deepseek-v4-flash` · n=5 · no rules

> **This label identifies the cell.** Four dataset × engine combinations coexist in P1 (TPC-DS/PG · TPC-DS/MySQL · IMDb/PG · IMDb/MySQL) — **never compare rows from cells with different labels** without stating in the text that the comparison is cross-engine or cross-benchmark.

> **The tables below are GENERATED from the store.** Do not hand-edit — regenerate. The narrative
> (conclusion at the top, threats to validity) is human and goes ABOVE this line.

**Setup recorded in the data:** FIND `qwen-reasoning+think+nohw+noplan` · WRITE `cloud:deepseek-v4-flash` · rules `off` · engine `postgres` · arm `aided`

**Window:** 2026-08-23T09:25 → 2026-08-26T09:18 · **108 REAL runs** across **21 queries** · **+58 baseline timeouts** (the client gave up before the pipeline ran — **not runs**, and `--resume` ignores them)

## Per-query table

| q | outcome (per run) | before → after | improvement | LLM inference (med) | FIND technique | index (best) | workload index |
|---|---|---|---|---|---|---|---|
| `q1` | L · L · L · L · L | 45.0 s → 2.5 s (floor) | **yes** ≥95.8% prov. | 313s (w 221s) | Rewrite Correlated Subquery wi · Index on store_returns (sr_ret | — | — |
| `q11` | to · mf · mf · to · to · to · to · mf · to · to · mf · to · to | — | no | 2223s (w 1729s) | Precompute Ratio Conditions in · Filter CTE by dyear in Main Qu | store_sales.ss_sold_date_sk 75.2% (measured) · 6 of 17 without measurement (omitted) | agg 1.3% (2 regr) |
| `q18` | to · to · ng · to · mf · to · ng · to · ng | 3.1 s → — | no | 2727s (w 1959s) | Filter date_dim early · Filter customer_address early | catalog_sales.cs_bill_customer_sk 12.0% (measured) · 3 of 19 without measurement (omitted) | agg 25.8% (0 regr) |
| `q25` | L · L · L · L · to · L | 118 ms → 25 ms | **yes** 78.9% | 477s (w 356s) | Index on date_dim for filterin · Composite index on store_sales | — | — |
| `q27` | to · L · to · ng · L · to · ng | 4.2 s → 3.3 s | **yes** 22.6% | 2555s (w 1875s) | Redundant Filter Simplificatio · Index on Filtered Columns | date_dim.d_year 8.6% (measured) | agg 2.6% (0 regr) |
| `q3` | L · to · to · L · L · to · to · ng · ng | 1.2 s → 179 ms | **yes** 85.2% | 536s (w 314s) | Join Order · Filter Early | item.i_manufact_id 8.4% (measured) · 2 of 6 without measurement (omitted) | agg 0.1% (0 regr) |
| `q30` | L · L · L · L · L | 45.0 s → 809 ms (floor) | **yes** ≥98.2% prov. | 450s (w 392s) | Materialize CTE · Filter date_dim early | — | — |
| `q38` | to · ng · to · ng · to · ng · to · ng · to · ng | 18.0 s → — | no | 1848s (w 1159s) | Pre-filter date_dim · Replace INTERSECT with aggrega | web_sales.ws_sold_date_sk, ws_bill_customer_sk 8.5% (measured) | agg 11.7% (0 regr) |
| `q40` | to · to · ng · ng · to · ng · to · to · ng · ng | 1.7 s → — | no | 4152s (w 2896s) | Precompute date_dim.d_date_sk  · Index on item.i_current_price | catalog_sales.cs_sold_date_sk 88.7% (measured) · 4 of 16 without measurement (omitted) | agg 19.9% (0 regr) |
| `q5` | to · L · ng · to · to · to · ng · ng · ng | 10.2 s → 5.5 s | **yes** 46.6% | 4413s (w 3108s) | Index Join Columns · Filter Early | — | — |
| `q50` | to · L · ng · ng · eq · ng | 530 ms → 509 ms | **yes** 3.9% | 1270s (w 924s) | pre-filter a large table as a  · Pre-filter date_dim for d2 | store_returns.sr_ticket_number 10.4% (measured) · 8 of 20 without measurement (omitted) | agg 0.0% (0 regr) |
| `q51` | to · to · to · ng · ng · to · ng · ng · to · mf · ng · to · ng · to · ng | 17.3 s → — | no | 3139s (w 2296s) | Precompute Cumulative Sums wit · Filter Nulls in Final Join | date_dim.d_month_seq 14.5% (measured) · 10 of 26 without measurement (omitted) | agg 0.1% (0 regr) |
| `q63` | to · eq · to · L · eq · to · ng · L | 6.1 s → 4.4 s | **yes** 28.0% | 2209s (w 1884s) | Eliminate Redundant Join with  · Precompute Date Filtered Date  | store_sales.ss_sold_date_sk 13.3% (measured) | agg 0.1% (0 regr) |
| `q67` | ng · to · ng · to · to · mf · to · ng · mf | 30.8 s → — | no | 3017s (w 1811s) | Eliminate ROLLUP Overhead · Precompute Aggregates | store_sales.ss_sold_date_sk 13.5% (measured) · 17 of 28 without measurement (omitted) | agg 0.1% (0 regr) |
| `q69` | L · L · L · L · L | 79.6 s → 11.6 s | **yes** 85.5% | 741s (w 607s) | Reorder Join Order · Composite Index on customer_ad | — | — |
| `q7` | L · L · L · L · L | 5.5 s → 2.8 s | **yes** 50.0% | 339s (w 281s) | Filter customer_demographics e · Pre-filter promotion | — | — |
| `q73` | to · ng · L · eq · to · to · L | 2.6 s → 2.4 s | **yes** 8.5% | 1705s (w 1034s) | Simplify ratio condition · Pre-filter store.s_county | store_sales.ss_store_sk 21.3% (measured) · 1 of 21 without measurement (omitted) | agg 0.1% (0 regr) |
| `q81` | L · L · L · L · L | 45.0 s → 23.1 s (floor) | **yes** ≥97.1% prov. | 323s (w 255s) | Rewrite Correlated Subquery as · Filter CTE Early | — | — |
| `q85` | to · ng · ng · to · ng · to · ng · to · ng | 430 ms → — | no | 2774s (w 2465s) | Split Complex OR Conditions in · Pre-filter Customer Demographi | web_sales.ws_sales_price, ws_net_profit 43.1% (measured) · 3 of 24 without measurement (omitted) | agg 6.7% (0 regr) |
| `q9` | L · L · L · L · L | 13.5 s → 3.7 s | **yes** 72.5% | 247s (w 208s) | Materialize Subqueries · Filter Early | — | — |
| `q96` | ng · ng · ng · to · ng · to · ng · to · ng | 2.4 s → — | no | 2252s (w 613s) | Filter Early · Pre-filter `time_dim` and `hou | store_sales.ss_hdemo_sk 6.4% (measured) | agg 0.0% (0 regr) |

**Legend** — outcome: `L` land · `ng` no gain · `mf` mechanical failure · `to` timeout · `eq`/`eqs` semantic/structural non-equivalence · `un` unverifiable.
**≥X%** is a **lower bound** (the original doesn't complete at real scale → the true gain is LARGER). `prov.` = **provisional** land, verified at reduced scale.
**Never compare `≥X%` with `X%`** — they are not the same ruler.
**before → after**: wall time of the ORIGINAL and of the REWRITE on the best land (this is where the % comes from). With no land, shows the median of the original — to separate a *query heavy for the DATABASE* from a *query hard for the MODEL*. `(floor)` = the original didn't complete, the time is the budget FLOOR.
**inference**: TOTAL LLM time per run (median), with the writer's share highlighted.
**index (best)**: `(measured)` = index actually created in a transaction, timed (warm-up discarded + median of 3) and undone by ROLLBACK · `(estimated)` = planner cost.
**The `workload index` column is ALWAYS ESTIMATED**, even when the one next to it is measured: it is the average of `(baseline_cost − cost_with_index)/baseline_cost` over the OTHER queries in the record — **planner cost, not time**. `agg X% (N regr)` = the others get X% cheaper on average, N get worse. **Do not read the two columns on the same ruler** — the planner overestimates (seen: 10.4% estimated × 0.8% measured on the same index).
**prov.**: the ORIGINAL doesn't complete within budget → no measured gain (only a lower bound) AND the equivalence check falls back to reduced scale, which **refutes but does not certify**. Both things have the same cause.


## Aggregate

- **reach 13** of 21 queries · **10 fully verified** · **3 provisional** (q1, q30, q81)
- outcomes: {'timeout': 61, 'no_gain': 47, 'rewrite_correct': 46, 'mechanics_failed': 8, 'equivalence_failed_semantic': 4}

> **ALWAYS report both numbers** — *"X validated, of which N provisional (verified at reduced scale because the original doesn't complete at real scale), and X−N fully verified"*. And **never** compare `≥X%` (lower bound) with `X%` (measured) as if they were the same metric.
