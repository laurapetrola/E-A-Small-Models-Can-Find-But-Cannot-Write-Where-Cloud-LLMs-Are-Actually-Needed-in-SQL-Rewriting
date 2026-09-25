# RAW cell of deepseek-r1 — `p1_deepseek_raw`

> **WHICH deepseek this is.** The tag `deepseek-r1:8b` looks generic, but Ollama repointed it to a
> **Qwen3** base in version 0528 — verified via `ollama show` (`architecture: qwen3`). In other words: this
> is **R1 distilled over Qwen3**, the "deepseek-qwen". **It is NOT** the
> `deepseek-r1:8b-llama-distill-q4_K_M`, which is distilled over Llama and **has no raw cell**.

**Setup:** `deepseek-r1:8b` (R1-distill/Qwen3) · reasoning **ON** (natural mode) · `noplan` · `nohw` ·
TPC-DS SF20 · PostgreSQL · **n=3** · **N REAL runs** *(the live count is in the table below)* on 21/21 queries **+14 baseline timeouts**.

**Window:** 30/07 → **26/08 05:14** — the cell went nearly a month **incomplete without anyone
knowing** (see "How this almost entered the paper wrong", below).

---

## CONCLUSION

### 1. Reach 7 of 21 — tied at the top of the raw panel (at n=5), but fragile in quality

> **CORRECTION 09/18 — "the best of the raw panel" is an ARTIFACT OF `n`.** The sentence compared
> `deepseek-r1` at **n=3** with `qwen` at **n=3**, when qwen was already at **n=5**. With the same yardstick
> they **TIE**:
>
> | arm | qwen | deepseek-r1 | |
> |---|---|---|---|
> | raw **at n=5** | **7**/21 | **7**/21 | **tie** |
> | raw at n=3 | 6/21 | 7/21 | r1 by 1 query |
> | aided-local (n=3) | **3**/21 | 2/21 | qwen |
> | aided-cloud (n=3) | **13**/21 | 12/21 | qwen |
>
> ⇒ **The correct sentence is "the two best models tie"**, not "r1 is the best". It wins in ONE
> arm by ONE query and loses in the other two. Do not write a ranking between qwen and r1 without stating the `n`.


| land | before → after | gain | class |
|---|---|---|---|
| **q9** | **15.0 s → 4.9 s** | **67.7%** | ROBUST — the only large, clean land |
| **q18** | 3.5 s → 3.1 s | 10.6% | ROBUST |
| q27 | 4.9 s → 4.6 s | 6.0% | ROBUST, but small gain |
| q40 | 1.9 s → 1.8 s | 5.6% | ROBUST, small gain |
| q1 | 45.0 s → 2.0 s | ≥95.6% | **provisional** — the original does not complete |
| q81 | 45.0 s → 1.3 s | ≥97.0% | **provisional** |
| q50 | 573 ms → 549 ms | 4.1% | MARGINAL (sub-second) |

**Reach 7, but only 2 robust, non-provisional lands** (q9 and q18). The two largest percentages
(q1 and q81) are **lower bounds** — the original blew the budget, so equivalence fell back
to SF1, which **refutes but does not certify**.

### 2. The raw panel, side by side

| cell | real runs | reach | `mechanics_failed` |
|---|---|---|---|
| **deepseek-r1** (R1/Qwen3, reasoning ON) | 63 | **7** | 22 (35%) |
| **qwen3:8b** (reasoning ON) | 65 | 6 | 18 (28%) |
| **llama3.1** (reasoning OFF) | 63 | 3 | 43 (68%) |
| **mistral** (reasoning OFF) | 63 | 1 | 42 (67%) |

**The split is by REASONING, not by size.** The two models that reason reach 6-7 of 21
and fail mechanically ~30% of the time; the two that don't reason reach 1-3 and fail ~67% of the time. All are
of the same 8B tier.

### 3. The cost of the advice is the highest in the panel

Median inference per run between **650 s and 1900 s** — q96 consumes **1893 s** (~32 min) and never
lands once; q9, which gives the best land, costs 1353 s. Do not confuse this with query weight: q96
executes in 2.4 s.

---

## Methodological defenses

- **14 baseline timeouts**, concentrated in q63 (3), q67 (3), q85 (3), q69 (2). They do not count toward the
  n — `--resume` repeats until it gathers 3 real runs. **No query was left without data.**
- **q1 and q81 are `prov.`** — the original does not complete at real scale, the gain is a **lower bound** and
  equivalence fell back to SF1. Never compare `≥95.6%` with a measured `%`: they are not the same yardstick.
- **q11 gave `un` (unverified) in all 3 runs** — the original blew up without a baseline hash, so the gate could
  neither approve nor reject it. This is the correct behavior since the S=1 fix; before that it would have become
  a false positive.
- **`pre-filter a large table as a CTE` dominates the FIND** (9 of the 21 queries) and almost never lands — the same
  default-response pattern observed in qwen raw.

---

## How this almost entered the paper wrong *(recorded 26/08)*

The cell had been **documented as closed for a month** and it was not:

| | reported | actually was |
|---|---|---|
| runs | 63 | **50** |
| queries with data | 21 | **18** |
| reach | 6 | **7** |

**The cause:** `cell_status.py` counted a real run as `writer or arm=="raw"`. In a **raw** cell there
is no writer by construction, so the `or` made **every** record count — including `timeout`s.
50 real + 13 timeouts = 63, which matched exactly the target of 21×3.

**q63, q67 and q85 had ZERO runs** — only timeout. And the **true reach was HIGHER** (7, not 6):
a query that looked like it wasn't landing simply had never been measured.

**Fixed:** the rule is now the same as `_done_counts` (run_matrix.py), which is what `--resume`
obeys — a `timeout` without a `writer` is not a run, in any arm. And `gen_cell_doc.py` now emits its
own alert for a query with zero real runs.

**Lesson for the paper:** counting files in the store is **not** counting runs. Every cell table
declares real runs **and** timeouts, and names any query below target.

<!-- GENERATED BELOW — do not edit; regenerate with scripts/gen_cell_doc.py -->

# Cell `p1_deepseek_raw` — generated by `scripts/gen_cell_doc.py`

## `TPC-DS` · `POSTGRES` · arm **`raw`** · writer: — (raw, one model does everything) · n=3 · no rules

> **This label identifies the cell.** Four dataset × engine combinations coexist in P1 (TPC-DS/PG · TPC-DS/MySQL · IMDb/PG · IMDb/MySQL) — **never compare rows from cells with different labels** without stating in the text that the comparison is cross-engine or cross-benchmark.

> **The tables below are GENERATED from the store.** Do not hand-edit — regenerate. The narrative
> (conclusion at the top, threats to validity) is human and goes ABOVE this line.

**Setup recorded in the data:** FIND `deepseek+think+nohw+noplan` · WRITE `RAW (one model does everything — no separate writer)` · rules `off` · engine `postgres` · arm `raw`

**Window:** 2026-07-30T00:02 → 2026-08-26T05:14 · **63 REAL runs** across **21 queries** · **+14 baseline timeouts** (the client gave up before the pipeline ran — **not runs**, and `--resume` ignores them)

## Per-query table

| q | outcome (per run) | before → after | improvement | LLM inference (med) | FIND technique | index (best) | workload index |
|---|---|---|---|---|---|---|---|
| `q1` | L · L · L | 45.0 s → 2.0 s (floor) | **yes** ≥95.6% prov. | 681s | decorrelate scalar aggregate s | — | — |
| `q11` | un · un · un | — | no | 1153s | pre-filter a large table as a  | 15 recommendation(s), **none measured** — planner estimate only | — |
| `q18` | mf · L · to · mf | 3.5 s → 3.1 s | **yes** 10.6% | 1273s | pre-filter a large table as a  · convert correlated subqueries  | catalog_sales.cs_sold_date_sk 7.3% (measured) · 27 of 37 without measurement (omitted) | agg 25.6% (0 regr) |
| `q25` | ng · ng · ng | 144 ms → — | no | 1034s | pre-filter a large table as a  | 34 recommendation(s), **none measured** — planner estimate only | — |
| `q27` | ng · ng · L | 4.9 s → 4.6 s | **yes** 6.0% | 1325s | pre-filter a large table as a  · join pushdown | 19 recommendation(s), **none measured** — planner estimate only | — |
| `q3` | ng · mf · ng | 158 ms → — | no | 706s | convert a correlated subquery  · reorder joins to reduce interm | 11 recommendation(s), **none measured** — planner estimate only | — |
| `q30` | mf · mf · mf | — | no | 1025s | use CTE for the subquery | 24 recommendation(s), **none measured** — planner estimate only | — |
| `q38` | ng · ng · ng | 16.8 s → — | no | 789s | split an OR predicate into UNI · convert INTERSECT operations i | 20 recommendation(s), **none measured** — planner estimate only | — |
| `q40` | L · to · mf · eq | 1.9 s → 1.8 s | **yes** 5.6% | 1125s | pre-filter a large table as a  · convert an IN subquery to a se | catalog_sales.cs_sold_date_sk 88.3% (measured) · 18 of 23 without measurement (omitted) | agg 19.5% (0 regr) |
| `q5` | mf · mf · mf | 11.1 s → — | no | 1437s | pre-filter a large table as a  | 18 recommendation(s), **none measured** — planner estimate only | — |
| `q50` | L · ng · ng | 573 ms → 549 ms | **yes** 4.1% | 1375s | pre-filter a large table as a  | 6 recommendation(s), **none measured** — planner estimate only | — |
| `q51` | mf · mf · mf | 16.1 s → — | no | 1165s | convert an IN subquery to a se | 6 recommendation(s), **none measured** — planner estimate only | — |
| `q63` | to · to · to · mf · mf · mf | 4.7 s → — | no | 902s | convert window functions to an | store_sales.ss_sold_date_sk 1.7% (measured) · 3 of 12 without measurement (omitted) | agg 0.0% (0 regr) |
| `q67` | to · to · to · mf · mf · mf | 31.3 s → — | no | 1485s | convert implicit join to expli · use rollup directly in the out | store_sales.ss_sold_date_sk 18.7% (measured) · 3 of 12 without measurement (omitted) | agg 0.1% (0 regr) |
| `q69` | ng · to · eq · to · eq | 730 ms → — | no | 1536s | convert IN subquery to semi-jo · join decomposition | customer_address.ca_state 1.0% (measured) · 25 of 28 without measurement (omitted) | agg 37.7% (0 regr) |
| `q7` | ng · ng · ng | 5.7 s → — | no | 653s | pre-filter a large table as a  | 9 recommendation(s), **none measured** — planner estimate only | — |
| `q73` | eq · eq · eq | 2.4 s → — | no | 981s | use inner join syntax instead  | 21 recommendation(s), **none measured** — planner estimate only | — |
| `q81` | mf · eq · to · L | 45.0 s → 1.3 s (floor) | **yes** ≥97.0% prov. | 1192s | convert an IN subquery to a se · use JOINs instead of subquerie | 12 recommendation(s), **none measured** — planner estimate only | — |
| `q85` | to · to · to · ng · ng · mf | 426 ms → — | no | 1356s | use explicit joins instead of  · convert implicit joins to expl | web_returns.wr_order_number 7.5% (measured) · 49 of 73 without measurement (omitted) | agg 6.7% (0 regr) |
| `q9` | L · L · L | 15.0 s → 4.9 s | **yes** 67.7% | 1353s | — | — | — |
| `q96` | ng · ng · ng | 2.4 s → — | no | 1893s | convert implicit joins to expl | 18 recommendation(s), **none measured** — planner estimate only | — |

**Legend** — outcome: `L` land · `ng` no gain · `mf` mechanical failure · `to` timeout · `eq`/`eqs` semantic/structural non-equivalence · `un` unverifiable.
**≥X%** is a **lower bound** (the original doesn't complete at real scale → the true gain is LARGER). `prov.` = **provisional** land, verified at reduced scale.
**Never compare `≥X%` with `X%`** — they are not the same ruler.
**before → after**: wall time of the ORIGINAL and of the REWRITE on the best land (this is where the % comes from). With no land, shows the median of the original — to separate a *query heavy for the DATABASE* from a *query hard for the MODEL*. `(floor)` = the original didn't complete, the time is the budget FLOOR.
**inference**: TOTAL LLM time per run (median), with the writer's share highlighted.
**index (best)**: `(measured)` = index actually created in a transaction, timed (warm-up discarded + median of 3) and undone by ROLLBACK · `(estimated)` = planner cost.
**The `workload index` column is ALWAYS ESTIMATED**, even when the one next to it is measured: it is the average of `(baseline_cost − cost_with_index)/baseline_cost` over the OTHER queries in the record — **planner cost, not time**. `agg X% (N regr)` = the others get X% cheaper on average, N get worse. **Do not read the two columns on the same ruler** — the planner overestimates (seen: 10.4% estimated × 0.8% measured on the same index).
**prov.**: the ORIGINAL doesn't complete within budget → no measured gain (only a lower bound) AND the equivalence check falls back to reduced scale, which **refutes but does not certify**. Both things have the same cause.


## Aggregate

- **reach 7** of 21 queries · **5 fully verified** · **2 provisional** (q1, q81)
- outcomes: {'no_gain': 21, 'mechanics_failed': 21, 'timeout': 14, 'rewrite_correct': 11, 'equivalence_failed_semantic': 7, 'unverified': 3}

> **ALWAYS report both numbers** — *"X validated, of which N provisional (verified at reduced scale because the original doesn't complete at real scale), and X−N fully verified"*. And **never** compare `≥X%` (lower bound) with `X%` (measured) as if they were the same metric.
