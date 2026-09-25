# I-MY — the 4th QUADRANT: `IMDb` × `MySQL`, and the 1st MySQL index measured by execution

> **CELL CLOSED 05/09 09:58** — 13/13 queries, **N REAL runs** *(the live count is in the table below)*, `exit 0`.
> **+10 baseline timeouts** — these are not runs; `--resume` ignores them.

**Setup:** FIND `qwen3:8b` (reasoning ON) · WRITE `deepseek-v4-flash` · **2 agents** · `noplan` ·
no schema · IMDb/JOB · **MySQL** · **n=3** · 04/09 10:02 → 05/09 09:58.

**Configuration identical to C1's and R1's** — only the coordinate changes. That is what closes the
`dataset × engine` matrix.

---

## THE RESULT — the 4-quadrant matrix is now complete

| | runs | queries | **reach** | lands | `no_gain` | `mech_f` | inference (med) |
|---|---|---|---|---|---|---|---|
| **I-MY** · `IMDb`/`MySQL` | 39 | 13/13 | **12/13** | 20 | 17 | **5%** | 204 s |
| **R1** · `IMDb`/`PostgreSQL` | 39 | 13/13 | 10/13 | 23 | 15 | 3% | 123 s |
| **C1** · `TPC-DS`/`PostgreSQL` | 108 | 21/21 | 13/21 | 46 | 47 | 7% | 335 s |

**The reach of 12/13 is the highest of any cell in the project** — and it comes from the quadrant
that had never been measured before.

### What this adds to axis (ii)

The paper's claim is that **the wall moves**. Before this cell, that movement had been measured by
**engine** (PG × MySQL, only on TPC-DS) and by **dataset** (TPC-DS × IMDb, only on PG). The corner
combining both was missing — and it **contradicts the simple reading**:

| | TPC-DS | IMDb |
|---|---|---|
| **PostgreSQL** | `mech_f` 7% | `mech_f` 3% |
| **MySQL** | (C4, in progress) | `mech_f` **5%** |

**Sentence this FORBIDS:** *"MySQL is the hard engine"*. On IMDb, MySQL has `mech_f` of 5% and
reach 12/13 — the best reach in the project. The difficulty that TPC-DS/MySQL shows **is not the
engine alone**: it is the combination with that workload (SF20, queries with heavy windowing and
aggregation).
**Sentence allowed:** *"the difficulty is from the COMBINATION of dataset × engine, not from a
single isolated factor."*

---

## THE INDEX — the finding that justified moving this cell to the front

This cell was **promoted ahead of C4** (decision 04/09) to answer an open question: *can MySQL
validate an index by EXECUTION?* Before it, the project's **19 MySQL cells had 100%
`validation: estimated`** — the "create the index and execute" path only existed in the PostgreSQL
backend, and was ported to MySQL on 03/09.

**The answer is yes, and by a wide margin:**

| | |
|---|---|
| `validation: executed` | **54** |
| `validation: estimated` | 22 |

**These are the project's first 54 MySQL index recommendations validated by execution.**

### And what the measurement reveals, that the estimate would NEVER reveal

| measured gain | |
|---|---|
| minimum | **−97.2%** |
| median | +2.5% |
| maximum | +99.2% |
| **how many get WORSE** | **16 of 54 (30%)** |

**Almost a third of the index recommendations, when measured by execution, DEGRADE the query** —
one of them nearly doubling the time. The advisor would recommend them; the stopwatch rejects them.

This speaks directly to what was already measured on PostgreSQL — 35-38% **sign disagreement**
between the planner's estimate and the real time — and closes the argument: **an index number based
on estimated cost is not a weak number, it is a number that gets the DIRECTION wrong in ~1/3 of
cases.**

**The remaining 22 `estimated`** are candidates whose measurement did not complete within budget
(`INDEX_MEASURE_TIMEOUT_S=600`). They **do not enter** any index claim — `gen_cell_doc.py` (04/09)
stopped printing a percentage when there is no measurement.

### What this decided, and saved

**The SF1 route is off the table.** There was a hypothesis of measuring MySQL indexes on the SF1
instance to work around the TPC-DS wall. It would cost code and would produce a number **not
comparable** with PostgreSQL (which measures on SF20, inside the transaction itself) — mixing engine
with scale. This cell shows that **it is not needed**: where the workload is executable, MySQL
measures natively.
**What gets stated:** *"on TPC-DS/MySQL (SF20) index recommendations are not validatable by
execution within a practical budget — 0 of 8 candidates completed. On IMDb/MySQL, with an executable
workload, 54 of 76 were measured."* This is a **scale limitation**, not an engine one.

---

## THREATS TO VALIDITY

- **n=3 is COVERAGE.** The consistency yardstick (≥3/5) does not apply. **Do not compare the
  reach of 12/13 with C1's 13/21** — C1 is n=5, and more runs mechanically means more chance of ≥1
  land. The honest comparison is **outcome rate** (`mech_f`, `no_gain`), which is insensitive to n.
- **13 queries.** Percentages over 39 runs have a wide interval; the `mech_f` of 5% is **2 runs**.
- **`1a` closed with 0 lands and 3 timeouts.** It is the FASTEST query in JOB (1.4 s of execution
  measured on 04/09), so the timeout **is not the query being heavy** — it is the re-strategy loop.
  It becomes a stated result, not evidence of query difficulty.
- **Operational contamination to state:** during this cell the campaign suffered **7 metadata
  lock deadlocks** (idle connections holding a lock), one of them costing 2h15 with no run at all.
  The failure mode records `baseline timeout`, which **looks like a model limit and is
  infrastructure**. This cell's 10 timeouts include that effect. Root cause fixed
  (`pool_recycle` in `src/connections.py`) and a watchdog in `scripts/campanhas/watchdog_locks.sh`.

<!-- GENERATED BELOW — do not edit; regenerate with scripts/gen_cell_doc.py -->

# Cell `p1cloud_qwen_flash_imdb_mysql` — generated by `scripts/gen_cell_doc.py`

## `IMDb/JOB` · `MYSQL` · arm **`aided-cloud`** · writer `cloud:deepseek-v4-flash` · n=3 · no rules

> **This label identifies the cell.** Four dataset × engine combinations coexist in P1 (TPC-DS/PG · TPC-DS/MySQL · IMDb/PG · IMDb/MySQL) — **never compare rows from cells with different labels** without stating in the text that the comparison is cross-engine or cross-benchmark.

> **The tables below are GENERATED from the store.** Do not hand-edit — regenerate. The narrative
> (conclusion at the top, threats to validity) is human and goes ABOVE this line.

**Setup recorded in the data:** FIND `qwen-reasoning+think+nohw+noplan` · WRITE `cloud:deepseek-v4-flash` · rules `off` · engine `mysql` · arm `aided`

**Window:** 2026-09-04T10:33 → 2026-09-05T09:58 · **39 REAL runs** across **13 queries** · **+10 baseline timeouts** (the client gave up before the pipeline ran — **not runs**, and `--resume` ignores them)

## Per-query table

| q | outcome (per run) | before → after | improvement | LLM inference (med) | FIND technique | index (best) | workload index |
|---|---|---|---|---|---|---|---|
| `10a` | to · mf · mf · to · L | 45.0 s → 13.7 s | **yes** 69.5% | 2177s (w 1607s) | reorder joins to reduce interm · Filter Title Early | title.production_year 37.1% (measured) · 2 of 6 without measurement (omitted) | agg 0.4% (6 regr) |
| `13d` | L · L · L | 32.6 s → 20.3 s | **yes** 37.7% | 595s (w 450s) | pre-filter a large table as a  · Filter Early with Indexes | company_name.country_code 15.7% (measured) | agg 4.4% (3 regr) |
| `16a` | ng · ng · L | 14.2 s → 3.3 s | **yes** 76.6% | 1727s (w 1503s) | Precompute Movie ID Filters · Index on Title.episode_nr | company_name.country_code 73.0% (measured) · 4 of 8 without measurement (omitted) | agg -2.4% (9 regr) |
| `17f` | L · ng · ng | 45.0 s → 19.4 s (floor) | **yes** ≥56.9% prov. | 1028s (w 861s) | pre-filter a large table as a  · convert an IN subquery to a se | name.name 6.7% (measured) · 2 of 4 without measurement (omitted) | agg -0.8% (3 regr) |
| `19b` | L · to · to · L · ng | 19.9 s → 12.2 s | **yes** 38.5% | 796s (w 736s) | Filter Early · Index on Title Table | title.title 52.3% (measured) | agg 0.3% (10 regr) |
| `1a` | to · to · to · ng · ng · ng | 952 ms → — | no | 1741s (w 1285s) | Pre-filter mi_idx by info_type · Index-Optimized Join on mc.com | info_type.info 99.2% (measured) | agg 1.4% (6 regr) |
| `22a` | L · to · ng · L | 4.1 s → 3.4 s | **yes** 18.7% | 816s (w 550s) | pre-filter a large table as a  · reorder joins to reduce interm | kind_type.kind 9.2% (measured) · 6 of 13 without measurement (omitted) | agg 5.0% (10 regr) |
| `23c` | L · to · ng · ng | 10.9 s → 2.0 s | **yes** 81.7% | 1305s (w 1046s) | Reorder joins to prioritize pr · Use derived table for movie_in | movie_info.info 24.4% (measured) | agg 8.3% (5 regr) |
| `29c` | to · ng · L · ng | 8.2 s → 7.0 s | **yes** 13.8% | 1729s (w 1392s) | pre-filter a large table as a  · Filter Early on Keyword Table | keyword.keyword 79.1% (measured) · 3 of 11 without measurement (omitted) | agg 85.7% (9 regr) |
| `2a` | ng · L · L | 12.1 s → 1.6 s | **yes** 87.1% | 806s (w 531s) | convert IN subquery to correla · pre-filter a large table as a  | company_name.country_code -38.1% (measured) · 1 of 2 without measurement (omitted) | agg -48.1% (4 regr) |
| `31b` | L · L · ng | 875 ms → 77 ms | **yes** 91.2% | 347s (w 307s) | pre-filter a large table as a  · reorder joins to reduce interm | title.production_year 2.8% (measured) · 3 of 7 without measurement (omitted) | agg -0.6% (9 regr) |
| `32a` | L · L · L | 622 ms → 0 ms | **yes** 100.0% | 1049s (w 872s) | Precompute Minimum Link Type · Filter Early on Linked Titles | — | — |
| `7b` | L · ng · ng | 429 ms → 371 ms | **yes** 13.5% | 1130s (w 833s) | pre-filter a large table as a  · create CTE for filterable subq | person_info.note 99.1% (measured) · 1 of 9 without measurement (omitted) | agg 8.7% (9 regr) |

**Legend** — outcome: `L` land · `ng` no gain · `mf` mechanical failure · `to` timeout · `eq`/`eqs` semantic/structural non-equivalence · `un` unverifiable.
**≥X%** is a **lower bound** (the original doesn't complete at real scale → the true gain is LARGER). `prov.` = **provisional** land, verified at reduced scale.
**Never compare `≥X%` with `X%`** — they are not the same ruler.
**before → after**: wall time of the ORIGINAL and of the REWRITE on the best land (this is where the % comes from). With no land, shows the median of the original — to separate a *query heavy for the DATABASE* from a *query hard for the MODEL*. `(floor)` = the original didn't complete, the time is the budget FLOOR.
**inference**: TOTAL LLM time per run (median), with the writer's share highlighted.
**index (best)**: `(measured)` = index actually created in a transaction, timed (warm-up discarded + median of 3) and undone by ROLLBACK · `(estimated)` = planner cost.
**The `workload index` column is ALWAYS ESTIMATED**, even when the one next to it is measured: it is the average of `(baseline_cost − cost_with_index)/baseline_cost` over the OTHER queries in the record — **planner cost, not time**. `agg X% (N regr)` = the others get X% cheaper on average, N get worse. **Do not read the two columns on the same ruler** — the planner overestimates (seen: 10.4% estimated × 0.8% measured on the same index).
**prov.**: the ORIGINAL doesn't complete within budget → no measured gain (only a lower bound) AND the equivalence check falls back to reduced scale, which **refutes but does not certify**. Both things have the same cause.


## Aggregate

- **reach 12** of 13 queries · **12 fully verified** · **0 provisional** (17f)
- outcomes: {'rewrite_correct': 20, 'no_gain': 17, 'timeout': 10, 'mechanics_failed': 2}

> **ALWAYS report both numbers** — *"X validated, of which N provisional (verified at reduced scale because the original doesn't complete at real scale), and X−N fully verified"*. And **never** compare `≥X%` (lower bound) with `X%` (measured) as if they were the same metric.
