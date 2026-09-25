# C2 — THE CEILING: `flash` finds and `flash` writes

> **CELL CLOSED 28/08 18:41** — 21/21 queries, **N REAL runs** *(the live count is in the table below)*, `exit 0`.
> **One caveat:** **q73** has **4 runs**, not 5 (an HTTP 500 during the container outage).
> 1 run is missing; `--resume` will pick it up. It doesn't change any conclusion below.
> **The seal says `aided-cloud` because the `writer` field is cloud — this is the CEILING cell.**
> What distinguishes it from C1 is the **FIND**: here who finds is `flash`. The label carries
> `+cloudfind`.

**Setup:** FIND `deepseek-v4-flash` · WRITE `deepseek-v4-flash` · **2 agents** · `noplan` ·
TPC-DS SF20 · PostgreSQL · **n=5** · 26/08 09:20 → 28/08 18:41 · **+32 baseline timeouts**.

**Reasoning held constant against C1** — verified via API call (25/08): qwen 8B reasons via the
`+think` toggle, `flash` reasons **natively**. **Do not write** that the cloud FIND runs without
reasoning: the `REASONING` variable is **inert** on that branch.

---

## THE RESULT

**C1 × C2 changes ONE thing: who finds.** The writer is the same `flash` in both.

| | C1 — qwen 8B finds | **C2 — flash finds** |
|---|---|---|
| runs | 108 | **104** |
| **reach** | 13/21 | **17/21** |
| **consistent** (≥3/5) | 8 | **12** |

### THE PARTITION — the main finding

| class | queries | reading |
|---|---|---|
| **tie — both land** | **11** — q1 q5 q7 q9 q25 q27 q30 q50 q63 q69 q81 | the 8B **finds**; the wall was **WRITE**, and the strong writer knocks it down |
| **only the ceiling lands** | **6** — q18 q38 q40 **q51** q67 q96 | the 8B **doesn't find**; the wall is **FIND** — the **same** writer resolves it with a better strategy |
| only C1 lands | 2 — q3, q73 | see "the anomaly", below |
| neither lands | 2 — q11, q85 | genuine ceiling of the approach |

**Where the 8B lands, the ceiling TIES in 11 of 11 — never improves.** Where the 8B **never**
lands, the ceiling finds a way in **6 of 8**. Both sides use the same writer: the difference is the
**FIND**.

### THE HEADLINE THIS FORCES US TO REWRITE

| | |
|---|---|
| **before** | *"on PostgreSQL the wall is WRITE"* |
| **after** | *"the wall moves location **depending on the query**: it is WRITE where the light model reaches the right strategy, and FIND where it doesn't"* |

**Stronger, not weaker.** The old version is an **average that lumps two populations together**.
The new one gives an **operational criterion** — *"does the 8B ever land this query?"* — that
separates the regimes and says **when decomposing pays off**, which is the engineering question.

  > **Ready-made sentence:** *"The bottleneck is not a property of the system but of the query. Where the
  > small model reaches the right strategy, the wall is writing, and a strong writer removes it. Where
  > it does not, the wall is finding, and the same writer succeeds only once a better strategy is
  > supplied. A single average over the workload conflates the two."*

### THE ANOMALY — q3 and q73, where the CEILING is WORSE

Two queries where the 8B lands and the ceiling does **not**. Do not sweep this under the rug: it
contradicts the direction of the finding and a reviewer will look exactly for this.

- **q73** has only **4 runs** — could be sampling. Complete it before interpreting.
- **q3** has 5 × 5 and is real: **C1 3/5 × C2 0/5**. Hypothesis to investigate: `flash` chooses a
  **different and worse** strategy — which would be evidence that *"finding better"* is not
  monotonic.
  Compare the proposed techniques in the two cells before writing anything.

### And a sentence ALREADY WRITTEN in the docs that DIES with this

**q51** was recorded as *"the qwen's only genuine ceiling"* — window-function non-equivalence,
**0 lands in 8 runs**. The ceiling lands it **3/5 with 75.3%** (18.9 s → 4.7 s). It is not a limit
of the **problem**: it is a limit of the **8B model**. Fix it wherever it appears.

---

## Methodological defenses

- **32 baseline timeouts** (don't count toward n; `--resume` repeats until 5 real ones accumulate),
  concentrated in q18 (5), q85 (5), q38 (4), q67 (4).
- **q1 is `prov.`** — the original doesn't complete at real scale; the gain is a lower bound and
  equivalence falls back to SF1, which **refutes but doesn't certify**.
- **The cost is almost entirely the WRITER's.** In the ceiling both agents reason, and it's the writer
  that dominates: q67 spends 3505 s/run with **2912 s (83%)** in the writer; q11, 2633 s with 2179 s
  (83%).
- **The cell went through the container outage on 28/08** (HTTP 500). Only q73 fell short of the
  target — the others were resumed by `--resume`. Infrastructure failure **gets re-run**;
  `mechanics_failed`/`no_gain`/`equivalence_failed` **do not** — those are the result.

---

## The cloud triple

| pair | changes | answers | state |
|---|---|---|---|
| **C1 × C2** | who **finds** | is the wall in FINDING? | **answered above** |
| **C2 × C3** | **1 agent × 2** | does decomposing help, with constant capacity? | C3 running |

<!-- GENERATED BELOW — do not edit; regenerate with scripts/gen_cell_doc.py -->

# Cell `p1cloud_ceiling_flash` — generated by `scripts/gen_cell_doc.py`

## `TPC-DS` · `POSTGRES` · arm **`aided-cloud`** · writer `cloud:deepseek-v4-flash` · n=5 · no rules

> **This label identifies the cell.** Four dataset × engine combinations coexist in P1 (TPC-DS/PG · TPC-DS/MySQL · IMDb/PG · IMDb/MySQL) — **never compare rows from cells with different labels** without stating in the text that the comparison is cross-engine or cross-benchmark.

> **The tables below are GENERATED from the store.** Do not hand-edit — regenerate. The narrative
> (conclusion at the top, threats to validity) is human and goes ABOVE this line.

**Setup recorded in the data:** FIND `qwen-reasoning+nothink+nohw+noplan+cloudfind` · WRITE `cloud:deepseek-v4-flash` · rules `off` · engine `postgres` · arm `aided`

**Window:** 2026-08-26T09:20 → 2026-09-07T01:43 · **105 REAL runs** across **21 queries** · **+33 baseline timeouts** (the client gave up before the pipeline ran — **not runs**, and `--resume` ignores them)

## Per-query table

| q | outcome (per run) | before → after | improvement | LLM inference (med) | FIND technique | index (best) | workload index |
|---|---|---|---|---|---|---|---|
| `q1` | L · L · L · L · L | 45.0 s → 1.9 s (floor) | **yes** ≥96.0% prov. | 392s (w 203s) | materialize intermediate resul · materialize CTE to avoid recom | — | — |
| `q11` | to · mf · mf · to · to · un · un · un | 45.0 s → — | no | 2633s (w 2179s) | materialize intermediate resul · pre-filter a large table as a  | 29 recommendation(s), **none measured** — planner estimate only | — |
| `q18` | to · eqs · to · mf · to · L · to · mf · to · ng | 2.6 s → 2.0 s | **yes** 22.5% | 3085s (w 2522s) | pre-filter a large table as a  · replace aggregate functions wi | catalog_sales.cs_sold_date_sk 40.0% (measured) · 15 of 55 without measurement (omitted) | agg 25.6% (0 regr) |
| `q25` | L · L · L · L · L | 142 ms → 36 ms | **yes** 74.9% | 818s (w 392s) | materialize date bounds as CTE · pre-filter tables into CTEs | — | — |
| `q27` | to · mf · L · L · L · L | 3.8 s → 1.7 s | **yes** 55.6% | 1234s (w 1136s) | pre-filter a large table as a  · materialize subquery into CTE | store_sales.ss_cdemo_sk 78.0% (measured) · 9 of 21 without measurement (omitted) | agg 0.3% (0 regr) |
| `q3` | ng · mf · ng · ng · eqs | 142 ms → — | no | 1663s (w 1232s) | pre-filter a large table as a  | date_dim.d_moy 12.1% (measured) · 7 of 20 without measurement (omitted) | agg 0.1% (0 regr) |
| `q30` | L · L · L · L · L | 45.0 s → 802 ms (floor) | **yes** ≥98.9% prov. | 635s (w 497s) | materialize intermediate resul · pre-filter a large table as a  | — | — |
| `q38` | to · to · L · mf · ng · to · ng · to · ng | 27.8 s → 14.4 s | **yes** 48.1% | 1719s (w 1195s) | convert INTERSECT to UNION ALL · materialize date filter into C | web_sales.ws_sold_date_sk 9.8% (measured) · 3 of 27 without measurement (omitted) | agg 34.0% (0 regr) |
| `q40` | ng · ng · to · ng · to · ng · L | 1.7 s → 1.6 s | **yes** 5.6% | 2236s (w 1774s) | pre-filter a large table as a  · materialize a subquery to pre- | catalog_sales.cs_sold_date_sk 79.4% (measured) · 3 of 5 without measurement (omitted) | agg 19.8% (0 regr) |
| `q5` | to · L · ng · to · L · ng · L | 9.2 s → 5.1 s | **yes** 44.9% | 1186s (w 967s) | pre-filter a large table as a  · materialize a subquery to avoi | 18 recommendation(s), **none measured** — planner estimate only | — |
| `q50` | L · L · L · L · L | 600 ms → 424 ms | **yes** 29.2% | 1194s (w 844s) | pre-filter a large table as a  · pull aggregate calculations in | — | — |
| `q51` | to · L · ng · L · to · ng · L | 18.9 s → 4.7 s | **yes** 75.3% | 1875s (w 1525s) | pre-filter a large table as a  · DATE-RANGE COVERING INDEX ACCE | 6 recommendation(s), **none measured** — planner estimate only | — |
| `q63` | L · L · L · to · ng · to · ng | 45.0 s → 24.6 s (floor) | **yes** ≥89.0% prov. | 2103s (w 1772s) | pre-filter a large table as a  · AGGREGATE-PUSHDOWN | store_sales.ss_sold_date_sk 1.1% (measured) · 6 of 8 without measurement (omitted) | agg 0.1% (0 regr) |
| `q67` | to · ng · to · to · ng · L · mf · to · ng | 30.5 s → 27.5 s | **yes** 10.0% | 3505s (w 2912s) | pre-filter a large table as a  · materialize intermediate resul | store_sales.ss_store_sk 2.8% (measured) · 18 of 28 without measurement (omitted) | agg 0.0% (0 regr) |
| `q69` | L · L · L · L · L | 80.6 s → 11.0 s | **yes** 86.3% | 423s (w 334s) | pre-filter a large table as a  · materialize intermediate resul | — | — |
| `q7` | L · L · L · L · L | 5.8 s → 2.2 s | **yes** 62.7% | 1082s (w 865s) | pre-filter a large table as a  · materialize filter subqueries  | store_sales.ss_promo_sk 1.9% (measured) · 3 of 6 without measurement (omitted) | agg 0.0% (0 regr) |
| `q73` | ng · ng · ng · to · ng · to · ng | 2.3 s → — | no | 2079s (w 1267s) | reorder joins to reduce interm · pre-filter a large table as a  | date_dim.d_year 7.5% (measured) · 9 of 34 without measurement (omitted) | agg 0.2% (0 regr) |
| `q81` | L · L · L · L · L | 45.0 s → 1.2 s (floor) | **yes** ≥97.7% prov. | 564s (w 357s) | decorrelate scalar aggregate s · WINDOW-AGGREGATE REPLACEMENT | — | — |
| `q85` | to · mf · to · mf · to · eqs · to · ng · to · ng | 420 ms → — | no | 2614s (w 2007s) | pre-filter a large table as a  · split an OR predicate into UNI | web_returns.wr_returning_cdemo_sk 11.8% (measured) · 25 of 79 without measurement (omitted) | agg 1.9% (0 regr) |
| `q9` | L · L · L · L · L | 15.1 s → 4.0 s | **yes** 73.4% | 244s (w 126s) | pull correlated subqueries int · pull up subqueries into derive | — | — |
| `q96` | ng · L · to · ng · ng · ng | 2.3 s → 1.4 s | **yes** 38.9% | 1360s (w 795s) | pre-filter a large table as a  · materialize intermediate resul | time_dim.t_hour 2.1% (measured) | agg 0.0% (0 regr) |

**Legend** — outcome: `L` land · `ng` no gain · `mf` mechanical failure · `to` timeout · `eq`/`eqs` semantic/structural non-equivalence · `un` unverifiable.
**≥X%** is a **lower bound** (the original doesn't complete at real scale → the true gain is LARGER). `prov.` = **provisional** land, verified at reduced scale.
**Never compare `≥X%` with `X%`** — they are not the same ruler.
**before → after**: wall time of the ORIGINAL and of the REWRITE on the best land (this is where the % comes from). With no land, shows the median of the original — to separate a *query heavy for the DATABASE* from a *query hard for the MODEL*. `(floor)` = the original didn't complete, the time is the budget FLOOR.
**inference**: TOTAL LLM time per run (median), with the writer's share highlighted.
**index (best)**: `(measured)` = index actually created in a transaction, timed (warm-up discarded + median of 3) and undone by ROLLBACK · `(estimated)` = planner cost.
**The `workload index` column is ALWAYS ESTIMATED**, even when the one next to it is measured: it is the average of `(baseline_cost − cost_with_index)/baseline_cost` over the OTHER queries in the record — **planner cost, not time**. `agg X% (N regr)` = the others get X% cheaper on average, N get worse. **Do not read the two columns on the same ruler** — the planner overestimates (seen: 10.4% estimated × 0.8% measured on the same index).
**prov.**: the ORIGINAL doesn't complete within budget → no measured gain (only a lower bound) AND the equivalence check falls back to reduced scale, which **refutes but does not certify**. Both things have the same cause.


## Aggregate

- **reach 17** of 21 queries · **14 fully verified** · **3 provisional** (q1, q30, q63, q81)
- outcomes: {'rewrite_correct': 58, 'timeout': 33, 'no_gain': 31, 'mechanics_failed': 10, 'unverified': 3, 'equivalence_failed_structural': 3}

> **ALWAYS report both numbers** — *"X validated, of which N provisional (verified at reduced scale because the original doesn't complete at real scale), and X−N fully verified"*. And **never** compare `≥X%` (lower bound) with `X%` (measured) as if they were the same metric.
