> ## BROUGHT INTO `final/p1` ON 21/08 — and why it matters more than it seemed
>
> This document was in `documentation/experiments/` and already declared itself *"for which paper: **P1
> (measurement)**"*. It is the **deterministic characterization** (no LLM) of WHERE the failure is born, and
> supports the P1 headline question via a different route than the ladder: instead of comparing arms, it
> **opens the failures up by origin**.
>
> **And its finding CONTRADICTS the PostgreSQL reading:** here **FIND is 51% of the wall**, WRITE
> only 21%. In PostgreSQL the conclusion is the reverse — WRITE is the wall. If this holds up, the
> answer to the headline question is **conditional on the engine**, which is a stronger (and more specific) finding
> than "the bottleneck is WRITE".
>
> **Caveats when citing:** the source cell is `…_aided_cloud_rwfocus` (writer `deepseek-chat`, earlier
> era, n=3), with FIND on `+think`. **Does not pair** with the PostgreSQL cells of the current composition. It is
> evidence of ORIGIN, not of count — and the comparison between engines requires the complete MySQL
> ladder (the `aided-local` rung is in the queue, local and free).
>
> In conversation with: [`RAW__tpcds-mysql__raw__qwen__planON.md`](RAW__tpcds-mysql__raw__qwen__planON.md) (raw, 8/21) and
> [`X-AC__tpcds-mysql__aidedcloud__qwen-CHAT-MORTO.md`](X-AC__tpcds-mysql__aidedcloud__qwen-CHAT-MORTO.md) (7/20), which show the single agent
> **tying or beating** the architecture on MySQL — the reverse of PostgreSQL.

# The aided-MySQL wall, classified by ORIGIN — what each failure is, and which lever (if any) attacks it

## SYNTHESIS · `TPC-DS` · `MYSQL` · classification of the semantic wall (EXEC bucket)


## WHAT THIS DOC ANSWERS
> **Question:** when the 2-agent setup fails on MySQL (aided, cloud writer), **where** is the failure born — in FIND (strategy), in WRITE (writing), or in neither (execution)? And **how much** of each?
> **Answer (summary):** of the 53 runs that did not land in M3-A, **~51% are FIND** (no-gain or non-equivalent strategy) · **21% WRITE** (dialect/scope/carelessness — fixable via prompt) · **17% execution** (timeout — not fixable) · **11% loop** (coder repeats). **The wall is predominantly FIND, not WRITE** — consistent with the `+truefb` null.
> **For which paper:** **P1 (measurement)** — it is the honest characterization that replaces the "raw buckets" (e.g. "18 mechanics") with *origin × lever*; it supports the local-FIND + cloud-masked-WRITE framing and the future-work claim "increase the finder".

> **Source:** store M3-A [`…_aided_cloud_rwfocus`], the **53 runs that did not land** (out of 63). DETERMINISTIC classification (no LLM): DBMS error code + `precheck_semantic_preservation` (separates coder-carelessness from FIND-wrong in the equiv-fails). Baseline for the numbers: [rewrite_focus_ab.md](rewrite_focus_ab.md).

## The central table — 53 failures by ORIGIN
| macro-origin | runs | % | what it is | lever |
|---|---|---|---|---|
| **FIND** | **27** | **51%** | the architect (qwen-8B) either didn't find a winning move, or found one that breaks equivalence | **finder capacity** — NOT fixable by a WRITE lever (it's the "increase the finder" case) |
| **WRITE** | **11** | 21% | the cloud-coder wrote invalid/non-equivalent SQL **given an OK strategy** | **D / SEM / SCHEMA_LINKING** (knowledge/attention missing from the prompt) |
| **EXECUTION** | 9 | 17% | timeout (slow query or runaway architect) | **none** — it's cost/scale, not a reasoning or writing error |
| **LOOP** | 6 | 11% | the coder re-emits the SAME SQL (temp=0 + non-actionable error) | symptom — dissolves once the root cause (the other origins) is addressed |

### Fine detail
| origin | sub-type | runs | error/signal |
|---|---|---|---|
| **FIND** | no-gain (no opportunity) | 20 | `no_gain` — valid SQL, equivalent, but **didn't speed up** |
| **FIND** | non-equivalent transf. | 7 | `equiv-fail` whose **strategy** already prescribed the transformation that breaks it (q30/q81 "filter early in the CTE") |
| **WRITE** | column scope | 5 | `1054 Unknown column` — pre-filtered in a CTE and forgot to carry the column → **SCHEMA_LINKING** |
| **WRITE** | dialect | 3 | `1055 ONLY_FULL_GROUP_BY` — instance config the model doesn't infer → **D** |
| **WRITE** | carelessness | 3 | `LIMIT` internally introduced by the coder (strategy didn't ask for it) → **SEM** |
| **EXEC** | slow query | 6 | `3024` MySQL timeout on the optimized version |
| **EXEC** | runaway architect | 3 | architect `timeout` (deliberated past the deadline) |
| **LOOP** | repeats the same SQL | 6 | `duplicate_attempt` |

## How each origin was decided (for reproducibility)
- **`timeout` / `3024`** → EXECUTION (cost, not writing).
- **`mechanics_failed`** → by the **optimized**'s error CODE (ignores the original's): `1055/1584/1064/1176` = WRITE-dialect · `1054` = WRITE-scope · `duplicate_attempt` = LOOP.
- **`equivalence_failed`** → `precheck_semantic_preservation(original, rewrite)`: if it flags an **internally introduced LIMIT** = WRITE-carelessness (the coder invented the LIMIT); anything else (dropped table / collapsed self-join / valid-but-S≠1) = FIND-wrong (the strategy's transformation doesn't preserve the result).
- **`no_gain`** → FIND-no-gain.

## By query — the 3 runs classified by origin (M3-A)
> Legend: `LAND` · `FIND-nogain` (valid, no gain) · `FIND-neq` (non-equivalent transf.) · `WRITE-dial` (dialect 1055/1584/1064) · `WRITE-scope` (1054 column) · `WRITE-limit` (internal LIMIT) · `EXEC` (timeout) · `LOOP` (repeats SQL). **Dominant origin** = most frequent macro-origin among the 3 (ignores LAND).

| q | run1 · run2 · run3 | dominant origin |
|---|---|---|
| q1 | LAND · LAND · LAND | **LAND** (3/3) |
| q3 | FIND-nogain · FIND-nogain · FIND-nogain | FIND |
| q5 | WRITE-scope · LAND · LAND | WRITE / LAND 2/3 |
| q7 | FIND-nogain · FIND-nogain · FIND-nogain | FIND |
| q9 | FIND-nogain · FIND-nogain · FIND-nogain | FIND |
| q11 | LOOP · EXEC · WRITE-scope | mixed |
| q18 | LOOP · FIND-nogain · FIND-nogain | FIND |
| q25 | LAND · EXEC · FIND-nogain | LAND 1/3 |
| q27 | FIND-nogain · FIND-nogain · LOOP | FIND |
| q30 | FIND-neq · FIND-neq · WRITE-scope | **FIND** (the float-SF1 #17) |
| q38 | WRITE-limit · WRITE-limit · WRITE-scope | **WRITE** (target SEM+schemalink) |
| q40 | EXEC · LOOP · EXEC | EXEC (timeout) |
| q50 | EXEC · LAND · FIND-neq | LAND 1/3 |
| q51 | WRITE-dial · WRITE-dial · WRITE-dial | **WRITE-dialect** (target D, deterministic) |
| q63 | FIND-nogain · LAND · FIND-neq | LAND 1/3 |
| q67 | EXEC · WRITE-scope · FIND-neq | mixed |
| q69 | LOOP · FIND-nogain · LOOP | FIND/LOOP |
| q73 | FIND-nogain · FIND-nogain · FIND-nogain | FIND |
| q81 | FIND-neq · EXEC · FIND-neq | **FIND** (non-equiv. transf.) |
| q85 | EXEC · LAND · EXEC | LAND 1/3 |
| q96 | WRITE-limit · FIND-nogain · LAND | WRITE / LAND 1/3 |

**Reading the table:** the **purely WRITE** queries (100% of the levers' target) are few — **q51** (3/3 dialect, the clean D case) and **q38** (2 LIMIT + 1 scope, SEM+schemalink target). The rest of WRITE appears **mixed** with FIND/EXEC within the same query (q5, q11, q67, q96) → the lever can fix ONE run and the query still doesn't land due to another origin. The **FIND-pure** queries (q3/q7/q9/q73 = `no_gain` 3/3) are the ones that "increase the finder" would have to tackle — and several may be "no opportunity" (see caveat 1).

## Threats to the validity of this classification
1. **The 20 `no_gain` are NOT all "weak FIND".** `no_gain` = valid, equivalent strategy that didn't speed things up. It could be **(a)** FIND too weak to find the move that EXISTS, **or (b)** there is no move (the query is already optimal — class-d no-op [[project_class_d_noop]]). **We cannot distinguish without an oracle** (e.g., a ceiling-FIND from the cloud landing where the 8B gave no_gain). So "51% FIND" is a **ceiling** — part of it is "no opportunity", not incapacity.
2. **FIND-wrong vs. WRITE-carelessness in the equiv-fails** depends on the `precheck` correctly identifying who introduced what. The precheck is conservative (0 false-positives on land, validated); but a "subtle" equiv-fail (precheck-clean) is attributed to FIND by elimination — there could be a coder carelessness the precheck doesn't catch.
3. **n=3 per query** — the counts are of runs, not queries; a stochastic query contributes different outcomes across different runs.
4. **A single store (M3-A).** The distribution could change with a different writer (local) or a different benchmark (IMDb).

## What this decides
- **The WRITE-levers run (D+SEM+SCHEMA_LINKING) targets 11/53 (21%).** A realistic, modest ceiling now quantified — it doesn't "fix the wall", it fixes the WRITE slice of it. Each lever in a disjoint sub-bucket → clean attribution even when combined.
- **The bulk (FIND, 51%) is left to the "increase the finder" axis** (future work), since no WRITE lever touches it and `+truefb` showed it isn't the feedback.
- **EXECUTION (17%) is a cost floor** of MySQL (slow engine, no parallel query) — it shrinks with a smaller scale or is an honest limit, not an optimization target.

## Pointers
- Levers and results: [rewrite_focus_ab.md](rewrite_focus_ab.md) (revealed the double wall) · [true_failure_feedback_ab.md](true_failure_feedback_ab.md) (FIND = capacity) · A/B of the WRITE-levers = `write_levers_ab.md` (to be generated).
- Architecture insight: [[project_write_fix_two_specialists]] · the double wall in [insights.md](../article/innovation_article/insights.md) (Insight 5).
