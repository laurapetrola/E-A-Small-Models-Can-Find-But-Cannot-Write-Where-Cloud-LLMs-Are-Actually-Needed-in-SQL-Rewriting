# C3 — THE MONOLITHIC CONTROL: `flash` alone, a single agent

> **CELL CLOSED 02/09 11:46** — 21/21 queries, **N REAL runs** *(the live count is in the table below)*, `== done: 15 ok, 0 failed`.
> **The seal below says `ceiling` and `writer: — (raw)` — read it as MONOLITHIC.** The generator
> classifies by the `writer` field, which here is empty *because there is no separate writer*: it's a
> single agent. The model label carries `+cloudfind`, and that's what distinguishes it from a local raw
> cell.
> **q38 has 7 records for 5 runs** (2 are baseline timeout, which `--resume` ignores).

**Setup:** a single `deepseek-v4-flash` does everything · **`TWO_AGENT_MODE=off`** · `noplan` ·
TPC-DS SF20 · PostgreSQL · **n=5** · 31/08 08:33 → 02/09 11:46.

**Full P1 regime:** `PLAN_HINTS=off · HW_HINTS=off · MASKING=off · SCHEMA_LINKING=off ·
APPLY_HEURISTICS=off` — the model receives **only the query's SQL and the dialect**. Same regime as C1
and C2, which is what makes them comparable.

---

## READ BEFORE CITING ANY NUMBER FROM THIS CELL

The numbers published internally until 02/09 (**reach 16, consistent 11, `mech_f` 2%**) were
**wrong in both directions at the same time**, and neither error showed up in the count:

| | what was there | effect |
|---|---|---|
| **contamination** | **q28 (5 runs) and q88 (5 runs)** — not part of the P1 corpus | INFLATED: both landed |
| **missing** | **q5, q50, q67** never ran | DEFLATED: all three land |

The cause was `control_monolithic.sh` scanning `queries/heldout_op2/` — the P2-era *fresh* set — to
reach q28, which was on the query list **of that** phase. The P1 corpus is 21 and q28 is not one of
them. So "100 runs, 20 queries" was not *20 of 21*: it was **18 from the corpus + 2 strangers**, and
C2×C3 was comparing **different query sets**.

**Fixed 02/09** (`documentation/fixed_bugs.md` **#20**): `heldout_op2` removed from `DIRS`; the 11
records moved — **not deleted** — to `.cache/_quarentena_p2_fora_corpus/`; and `cell_status.py` now
distinguishes **MIXTURE** (part corpus, part outside — the dangerous case) from **OTHER CORPUS**
(a whole cell from another set, like the fresh batch, where the "outside" queries are the point).

**The lesson this leaves for the rest of the panel:** the checking question is never *"how many
queries does the cell have"* — it's ***"are they the SAME ones as the cell I'm going to compare it
to"***. A partial cell at least announces itself; a contaminated cell has a full count and **looks
healthy**.

---

## THE RESULT

**C2 × C3 changes ONE thing: whether the architecture decomposes.** The model is the same `flash` in
both, the regime is the same, and the corpus is now the same.

| | C2 — decomposed (find write) | **C3 — monolithic (one agent)** |
|---|---|---|
| runs | 104 | **105** |
| **reach** | 17/21 | **17/21** |
| **consistent** (≥3/5) | 12 | **12** |
| **`mechanics_failed`** | 10% | **3%** |
| inference (median/run) | 319 s | 370 s |

### THE TIE — and it is exact

| class | queries | reading |
|---|---|---|
| **tie — both land** | **16** — q1 q5 q7 q9 q18 q25 q27 q30 q38 q40 q50 q51 q63 q67 q69 q81 | decomposing didn't change the outcome |
| only the **decomposed** lands | **1** — q96 | gain from decomposition |
| only the **monolithic** lands | **1** — q3 | loss from decomposition |
| neither lands | **3** — q11 q73 q85 | genuine ceiling of the approach |

**With a strong model, decomposing doesn't buy reach: 17 = 17, 12 = 12, and the per-query swap is
1 × 1.** And the monolithic one still **writes better** — `mechanics_failed` at **3% versus 10%**.

### THE SENTENCE THIS FORCES US TO WRITE

| | |
|---|---|
| **cannot be written** | *"decomposing the task improves the result"* — on `flash`, in TPC-DS/PG, it doesn't improve |
| **can be written** | *"decomposing pays off where capacity is SCARCE; with a strong model the monolithic ties in reach — and writes better"* |

**This is a NEGATIVE result, and it is good for a MEASUREMENT paper.** P1 is E&A: a measured tie
with n=5 over an identical corpus, with inference cost practically equal (10.6 h × 10.8 h), is a
discovery — not a failure. What it prohibits is selling decomposition as a universal gain.

### AND WHY THE DECOMPOSED ONE WRITES WORSE

`mechanics_failed` 10% × 3% is the sharpest difference in the table, and it has a structural
explanation: in C2 the writer receives a strategy in **natural language** and needs to implement it;
in C3 the model writes what **it itself** planned. **The handoff between the two agents costs
mechanics.**
This is a hypothesis consistent with the number, **not a measurement** — separating handoff from
other causes would require an arm where the same strategy is delivered in both formats.

---

## WHERE THIS CELL FITS IN THE TRIPLE

| | C1 — 8B finds, flash writes | C2 — flash finds, flash writes | **C3 — flash alone** |
|---|---|---|---|
| architecture | decomposed | decomposed | **monolithic** |
| who finds | qwen 8B local | `flash` | `flash` |
| **reach** | 13/21 | 17/21 | **17/21** |
| **consistent** | 8 | 12 | **12** |
| `mechanics_failed` | 7% | 10% | **3%** |

**Reading the triple together separates TWO things that tend to be confused:**

- **C1 × C2** isolates **who finds** (same architecture, different models) → **capacity matters: 13 → 17.**
- **C2 × C3** isolates **the architecture** (same model, decomposes or not) → **decomposition doesn't matter: 17 = 17.**

**Do not collapse the two readings.** The gain from 13 → 17 belongs to the MODEL, not to
decomposition — the C2 × C3 line shows that decomposition alone, with capacity to spare, delivers zero
reach.

---

## THREATS TO VALIDITY

- **Scope:** TPC-DS/PostgreSQL/SF20, one cloud model, 21 queries. **Do not** generalize to MySQL or
  to local models — C1 shows that with scarce capacity the picture is different.
- **The tie is about REACH, not rewrite quality.** Average gain and discovered technique are not
  compared here; the per-query table (below) carries the gains by outcome.
- **`mech_f` 3% × 10% comes from n=5 per query.** It's a consistent difference in direction, but the
  interval is wide — report it as *"writes better"*, not with a decimal.
- **q11, q73, and q85 don't land in any of the three arms** — they are candidates for a genuine wall,
  and q73 lands in C1 (2/5), which makes it the most unstable case of the three.
- **The 2 baseline timeouts of q38 are not runs** and `--resume` ignores them; the cell has 5 real
  runs for it, not 7.

<!-- GENERATED BELOW — do not edit; regenerate with scripts/gen_cell_doc.py -->

# Cell `control_monolithic_flash_p1` — generated by `scripts/gen_cell_doc.py`

## `TPC-DS` · `POSTGRES` · arm **`ceiling`** · writer: — (raw, one model does everything) · n=5 · no rules

> **This label identifies the cell.** Four dataset × engine combinations coexist in P1 (TPC-DS/PG · TPC-DS/MySQL · IMDb/PG · IMDb/MySQL) — **never compare rows from cells with different labels** without stating in the text that the comparison is cross-engine or cross-benchmark.

> **The tables below are GENERATED from the store.** Do not hand-edit — regenerate. The narrative
> (conclusion at the top, threats to validity) is human and goes ABOVE this line.

**Setup recorded in the data:** FIND `qwen-reasoning+nothink+nohw+noplan+cloudfind` · WRITE `RAW (one model does everything — no separate writer)` · rules `off` · engine `postgres` · arm `aided`

**Window:** 2026-08-31T08:33 → 2026-09-02T11:46 · **105 REAL runs** across **21 queries** · **+2 baseline timeouts** (the client gave up before the pipeline ran — **not runs**, and `--resume` ignores them)

## Per-query table

| q | outcome (per run) | before → after | improvement | LLM inference (med) | FIND technique | index (best) | workload index |
|---|---|---|---|---|---|---|---|
| `q1` | L · L · L · L · L | 45.0 s → 2.0 s (floor) | **yes** ≥95.9% prov. | 40s | decorrelate scalar aggregate s · materialize intermediate resul | — | — |
| `q11` | un · un · un · un · mf | 45.0 s → — | no | 442s | pre-filter a large table as a  · reorder joins to reduce interm | — | — |
| `q18` | eqs · mf · L · ng · ng | 3.4 s → 2.8 s | **yes** 19.3% | 822s | pre-filter a large table as a  · materialize intermediate resul | — | — |
| `q25` | L · L · L · L · L | 45.0 s → 5.0 s (floor) | **yes** ≥90.2% prov. | 222s | pre-filter a large table as a  · reorder joins to reduce interm | — | — |
| `q27` | ng · L · L · L · L | 4.0 s → 3.0 s | **yes** 26.4% | 278s | pre-filter a large table as a  · materialize intermediate resul | — | — |
| `q3` | ng · L · ng · L · ng | 188 ms → 161 ms | **yes** 14.3% | 478s | pre-filter a large table as a  · materialize filter subquery in | — | — |
| `q30` | L · L · L · L · L | 45.0 s → 648 ms (floor) | **yes** ≥98.7% prov. | 160s | convert an IN subquery to a se · replace correlated subquery wi | — | — |
| `q38` | L · to · ng · L · to · ng · ng | 20.1 s → 16.5 s | **yes** 18.2% | 521s | convert INTERSECT to UNION ALL · materialize intermediate resul | — | — |
| `q40` | eqs · ng · L · ng · L | 1.6 s → 1.5 s | **yes** 6.9% | 436s | pre-filter a large table as a  · reorder joins to reduce interm | — | — |
| `q5` | L · L · L · eq · L | 10.7 s → 6.1 s | **yes** 43.5% | 444s | pre-filter a large table as a  · reorder joins to reduce interm | — | — |
| `q50` | L · eq · eq · L · L | 508 ms → 456 ms | **yes** 10.2% | 408s | reorder joins to reduce interm · pre-filter a large table as a  | — | — |
| `q51` | L · L · L · L · L | 15.8 s → 5.2 s | **yes** 67.3% | 241s | reorder joins to reduce interm · split an OR predicate into UNI | — | — |
| `q63` | ng · ng · L · ng · L | 5.1 s → 4.7 s | **yes** 9.1% | 591s | reorder joins to reduce interm · convert IN subquery to range w | — | — |
| `q67` | mf · L · L · L · ng | 28.6 s → 23.7 s | **yes** 17.2% | 537s | reorder joins to reduce interm · pre-filter a large table as a  | — | — |
| `q69` | L · L · L · L · L | 75.3 s → 4.3 s | **yes** 94.2% | 124s | materialize common subquery in · materialize intermediate resul | — | — |
| `q7` | L · L · L · L · L | 5.7 s → 2.9 s | **yes** 49.0% | 138s | pre-filter a large table as a  · materialize intermediate resul | — | — |
| `q73` | ng · ng · ng · ng · ng | 2.2 s → — | no | 439s | pre-filter a large table as a  · materialize intermediate resul | — | — |
| `q81` | L · L · L · L · L | 45.0 s → 1.2 s (floor) | **yes** ≥97.7% prov. | 163s | convert an IN subquery to a se · replace correlated subquery wi | — | — |
| `q85` | ng · ng · ng · ng · ng | 465 ms → — | no | 692s | pre-filter a large table as a  · convert an OR predicate into U | — | — |
| `q9` | L · L · L · L · L | 15.5 s → 3.9 s | **yes** 75.0% | 28s | replace correlated subqueries  · convert correlated subqueries  | — | — |
| `q96` | ng · ng · ng · ng · ng | 2.2 s → — | no | 425s | reorder joins to reduce interm · materialize a subquery into a  | — | — |

**Legend** — outcome: `L` land · `ng` no gain · `mf` mechanical failure · `to` timeout · `eq`/`eqs` semantic/structural non-equivalence · `un` unverifiable.
**≥X%** is a **lower bound** (the original doesn't complete at real scale → the true gain is LARGER). `prov.` = **provisional** land, verified at reduced scale.
**Never compare `≥X%` with `X%`** — they are not the same ruler.
**before → after**: wall time of the ORIGINAL and of the REWRITE on the best land (this is where the % comes from). With no land, shows the median of the original — to separate a *query heavy for the DATABASE* from a *query hard for the MODEL*. `(floor)` = the original didn't complete, the time is the budget FLOOR.
**inference**: TOTAL LLM time per run (median), with the writer's share highlighted.
**index (best)**: `(measured)` = index actually created in a transaction, timed (warm-up discarded + median of 3) and undone by ROLLBACK · `(estimated)` = planner cost.
**The `workload index` column is ALWAYS ESTIMATED**, even when the one next to it is measured: it is the average of `(baseline_cost − cost_with_index)/baseline_cost` over the OTHER queries in the record — **planner cost, not time**. `agg X% (N regr)` = the others get X% cheaper on average, N get worse. **Do not read the two columns on the same ruler** — the planner overestimates (seen: 10.4% estimated × 0.8% measured on the same index).
**prov.**: the ORIGINAL doesn't complete within budget → no measured gain (only a lower bound) AND the equivalence check falls back to reduced scale, which **refutes but does not certify**. Both things have the same cause.


## Aggregate

- **reach 17** of 21 queries · **14 fully verified** · **3 provisional** (q1, q25, q30, q81)
- outcomes: {'rewrite_correct': 63, 'no_gain': 30, 'unverified': 4, 'mechanics_failed': 3, 'equivalence_failed_semantic': 3, 'equivalence_failed_structural': 2, 'timeout': 2}

> **ALWAYS report both numbers** — *"X validated, of which N provisional (verified at reduced scale because the original doesn't complete at real scale), and X−N fully verified"*. And **never** compare `≥X%` (lower bound) with `X%` (measured) as if they were the same metric.
