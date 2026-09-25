# Known & fixed bugs — false positives of the machinery (measurement integrity)

> **Why this doc exists.** The executor's Phase A (decompose parser + AST structural guard) are
> **cheap proxies** for equivalence. They sometimes **fabricate a failure over a VALID rewrite** —
> and that, if not caught, becomes a false "model ceiling" in the measurement (the model got it right; our guard
> rejected it). What decides equivalence for real is the **S=1 gate (result-set hash)**.
>
> **Rule (methodology):** before nailing down "model limit" on a cell, check whether the guard/parser
> fabricated the failure. Every bug here was **fixed + tested** (`tests/test_structural_integrity.py`). Each
> case becomes evidence that the failure was ours, not the model's.

| # | Query(s) | Symptom | Root cause | Fix | Test | Date |
|---|---|---|---|---|---|---|
| 1 | **q38** (TPC-DS) | `equivalence_failed_structural`: "numeric constant '11' missing" over a correct rewrite | Original had `d_month_seq between 1189 and 1189 + 11`; the model **folded** it (`= 1200`, correct). The guard required literal `11` to be present → flagged "missing constant" over a **valid constant-fold**. | The guard only flags a **PURE drop** (constant disappears AND **no** new literal appears, e.g. `*1.2` dropped). If there's a new literal (`1200`), it was folded → let **S=1** judge it. | `test_constant_fold_is_not_flagged` | 2026-06-20 |
| 2 | **q38** (INTERSECT), **q69** (EXISTS) | `Cartesian_Join_Detected` (`JOIN base_table ON TRUE`) over rewrites that did not actually have a Cartesian product | The **decompose parser** lifted the tables from the branches (INTERSECT channels / EXISTS subquery) into the outer `FROM` **without the join predicate** → `reconstruct` emitted `ON TRUE`. It was the **parser breaking the structure**, not the model. | Route `INTERSECT`/`EXCEPT`/`UNION`/`EXISTS` to **free-form** (preserves the structure). *(Today, with the architect always free-form, decompose is dormant → the bug can no longer occur.)* | `test_base_table_cross_join_still_detected` (the REAL Cartesian product is still caught) | 2026-06-14 |
| 3 | **q9** (TPC-DS) | `CROSS JOIN LATERAL` for an aggregate (consolidation of scalar subqueries) flagged as Cartesian + unrecognized alias | The guard treated `CROSS JOIN LATERAL` (the natural form for consolidating N scalar subqueries in one pass) as a Cartesian product. | Recognize `CROSS JOIN [LATERAL]` for a 1-row aggregate as valid (non-Cartesian); recognize the alias. | `test_cross_join_lateral_aggregate_not_flagged` | 2026-06-14 |
| 4 | **q7** (TPC-DS) | `Data_Loss_Detected: table 't' removed` when the model uses `WITH t AS (SELECT * FROM t WHERE ...)` | The guard counted base tables only in the outer FROM; a CTE with the **same name** as the base table (which is still scanned **inside** the CTE) was read as "table removed". | Also count base tables **inside CTE bodies** → the scan inside the CTE counts. | `test_cte_named_like_base_table_is_not_data_loss`, `test_plain_cte_inlining_is_not_data_loss` | 2026-06-14 |
| 5 | **q11** (TPC-DS, held-out) | `Data_Loss_Detected: filter 't_s_secyear.dyear = 1998 + 1' was removed or altered` over an equivalent rewrite | The model split `year_total` into per-year CTEs and wrote the outer filter as `dyear = 1999` — exactly `1998 + 1` **folded**. The **filter-preservation** guard compared by **TEXT** and didn't fold the arithmetic → read the filter as "removed". (Same root as #1, but in **another guard**: filter, not numeric constant.) | **Constant-fold** the original filter (`sqlglot.simplify`) before matching — matches the folded form (direct and EQ-reversed). Additive: only removes false positives, never creates misses. A **genuine drop** of the filter (without folding) still fires. | `test_folded_filter_constant_is_not_data_loss`, `test_genuinely_dropped_filter_still_detected` | 2026-06-20 |
| 6 | **q11** (TPC-DS, held-out) — **architectural change, generalizes #5** | The structural guard gave a **HARD-REJECT before execution**: the 2nd form of q11 (splitting `year_total` into CTEs by `(sale_type, year)` — re-encodes `sale_type='s'`→table `store_sales`, `dyear`→internal CTE filter) made the textual guard flag **all 8 filters** as removed. **Manual S=1 confirmed EQUIVALENT** (identical hash) → false positive blocking a valid rewrite. | The guard became **ADVISORY**: the executor **no longer returns early**; the rewrite **reaches S=1** (real authority = execution + exact cardinality). Structural findings resurface as a **reflection hint IF S=1/execution fails**, and as a transparent **`structural_caveat`** IF S=1 approves. (#5 was a point-fix; #6 makes **every** structural false positive harmless.) | `test_structural_integrity.py` (detector still detects) + integration validation = q11 re-run | 2026-06-20 |
| 7 | **q1** (TPC-DS, **MySQL**) — **veto by COST, not Phase-A** | `cost_gate_error` REJECTED a **VALID (hash) and 91% FASTER** rewrite (`mechanics_failed`). The gate only vetoes on cost *if the time didn't improve* — but it checked `improvement_pct`, which is **`None` when the original TIMES OUT** (only `improvement_lower_bound_pct` is set). Result: it treated "91% via lower-bound" as "didn't improve" → applied the veto because the **MySQL planner over-estimates the cost of the rewrite ~3000×** (cost +305,000%). **It's the EXACT veto-by-cost the thesis says NEVER to do** — and the proof of cost≠time in MySQL. *(PG was unaffected: the planner estimates the original as expensive → cost DECREASES → no veto; check confirmed 0 PG instances of the pattern.)* | `cost_gate_error` now honors **both** `improvement_pct` AND `improvement_lower_bound_pct`: if either is > 0, time improved → **no veto** (real time overrules cost, including the timeout case). **NECESSARY but INSUFFICIENT — completed by #8 (ORDER bug in the executor).** | `test_cost_gate.py` (4 cases: lower-bound not vetoed, exact not vetoed, slow+cost↑ vetoed, cost↓ never vetoes) | 2026-06-22 |
| 9 | **ALL** (reasoning axis) — **thinking flag not applied (silent config)** | The "reasoning models" ran with thinking **TURNED OFF**: the reasoning on/off axis **was not controlled** → the qwen×mistral contrast was, in reality, a **BASE comparison** (not a reasoning one). And even in the `qwen+think` runs, **the index node still ran without reasoning** → records labeled `+think` whose **index ran nothink** (mislabel on the reasoning axis). | `get_llm()` called **WITHOUT** the reasoning flag → Ollama defaults to `think:false`. In **TWO** nodes: **architect** (fixed 2026-06-23) and **index_advisor** (fixed 2026-06-24 — **same pattern, second call-site forgotten**, same as #8). | `get_llm(reasoning=_reasoning_flag())` in both nodes; **`REASONING=on\|off`** controls it explicitly; CoT captured (architect). `_reasoning_flag` is a single source, imported in both. | **no automated test yet (gap)** — verified manually: `<think>` captured (architect) + import/flag honored (index). | 2026-06-23 / 24 |
| 10 | **index — state keys DROPPED (silent loss)** | `index_reasoning_trace` always saved as `None` (the index's CoT never persisted, despite the whole streaming-vs-invoke saga); and `index_workload_aware` always saved as `False`-default → in **Form 2** (workload on) it would collide with Form 1 = the two-forms experiment **useless**. | The keys **were not declared in the `PipelineState`** (TypedDict) → LangGraph **drops unknown keys** when merging the node's output → they never reached `input_router`. `index_advisor_inference_ms`/`index_specs` (declared) flowed through → masking the problem (it looked like only the trace failed). | Declare `index_reasoning_trace: NotRequired[str]` + `index_workload_aware: NotRequired[bool]` in `state.py`. Lesson: **any new key a node returns AND needs to survive the graph MUST be in PipelineState** (otherwise it disappears with no error). **Audit revealed this is a PATTERN — 3 other dropped keys:** `architect_timed_out` (the serious one: the architect's 450s timeout becomes `outcome="timeout"` at input_router:80, but it was dropped → NEVER fires → architect timeouts classified as `mechanics_failed`/class-b → **inflates b, undercounts timeout**; worse in slow/reasoning models); `decomposed_provenance` (arrives empty at the saver — low impact, it's always free_form); `from_corrector` (always False — benign, corrector dormant). All declared now. **Non-recoverable** past data for architect_timed_out (the flag wasn't saved) → caveat: part of the `mechanics_failed` may be architect timeout; future is fine. | manual (no contaminated Form-2 data — fix landed before it) | 2026-06-24 |
| 9b | **index, reasoning ON** — **greedy parser breaks with `<think>` (coupled to #9)** | `_extract_index_specs` used `re.search(r'\{.*\}', text, DOTALL)` **greedy, without stripping `<think>`** (= the bug from the labeler [[project_labeler_no_cot]], which was fixed there and NOT here). **Dormant** while #9 kept the index nothink; would **wake up** on turning reasoning on: the keys inside `<think>` would make the greedy match (1st `{` → last `}`) span think+response → invalid JSON → `None` → **EMPTY index**. So turning on only #9 (get_llm) **without this fix would make it worse** (empty specs instead of an index). | Greedy match without sanitizing the CoT. | Strip closed `<think>…</think>` blocks + cut junk before an unclosed `</think>`, **before** the match. Tested off/on+think/unclosed. | manual test (3 cases in the fix) — no regression test in the suite yet | 2026-06-24 |
| 11 (audit **H1**) | **ALL — MySQL** (S=1 WEAKER than in PG) | S=1's **exact cardinality** cross-check (catches divergence BEYOND the 100-row sample) **never ran on MySQL** → S=1 = sample-hash only. A rewrite that changes rows beyond 100 passes in MySQL but would be caught in PG → **the cross-engine comparison = machinery artifact**. | `top_node_actual_rows` returns `None` at the base ("engines override"); **PG overrides it, MySQL does NOT** → the gate is a no-op in MySQL. | Override in `MySQLBackend`: parses `rows=Z` from the EXPLAIN ANALYZE root node (`plan_text`, 1st timed line). | manual (pure function: root_rows→5, not the 58M of the scan) | 2026-06-25 |
| 12 (audit **H2**) | **ALL — MySQL** (timing could be SILENT wall-clock) | `execution_time_ms = _parse_explain_analyze_time(...) or elapsed_ms`: the parser only looked at `splitlines()[0]`; with a blank line/wrapper before the root, it failed → fell back to the **Python clock** (parse+network+fetch, NOT the engine's time), **without warning** → corrupts `improvement_pct` (the headline metric) in an indistinguishable way. | Fragile parser (only 1st line) + silent fallback. | Parser scans **all** lines for the 1st `actual time=` (root); records **`timing_source: engine\|wallclock`** → wall-clock lines become flaggable/excludable. | manual (parser w/ blank line → 456.7, not None→wallclock) | 2026-06-25 |
| 13 (audit **H3**) | **MySQL** — cost UNDERESTIMATED for CTE/decorrelation rewrites | `get_total_cost` only read the **outer** `query_block` → the cost of materialized CTEs/derived tables (where decorrelation/consolidation **moves the work** — exactly the paper's techniques) was left out → the cost gate lets through a rewrite that should be blocked; bias in the cost≠time finding for MySQL. | No recursion over nested `query_block`s. | `_collect_mysql_costs` walks the whole tree; `get_total_cost` returns the **MAX** query_cost (never below the most expensive sub-block; MAX≠sum → no double-count). | manual (nested CTE 5000 vs outer 100 → max=5000) | 2026-06-25 |
| 14 (audit **H4**) — **ENGINE AGNOSIA** | **MySQL** — PG index guidance leaking into the MySQL model's prompt | `causal_regression_hint` (shared) suggested **"trigram/GIN index"** (a **PG** concept) and that reached the **MySQL LLM** as reflection error → wrong guidance (MySQL=FULLTEXT) + cross-engine bias (different amount of help injected per engine). | Hardcoded PG index term in the shared hint. | `_nonsargable_remedy()` per engine: PG=`"trigram/GIN"`, MySQL=`"FULLTEXT"`, base=neutral. Only this term was dialect-specific (B-tree is common to both). | live (PG remedy="trigram/GIN") | 2026-06-25 |
| 15 (audit **M1**) | **ALL (PG+MySQL)** — non-deterministic `ORDER BY 1` in sample_hash | `SELECT * FROM (q) ORDER BY 1 LIMIT 100`: sorts only by the **1st column** → in **ties at the 100-row boundary**, WHICH ones make it in is plan-dependent → 2 equivalent queries can **hash differently** = **FALSE S≠1** (fabricated class-b). Python's `sorted()` only re-sorts what the LIMIT already brought in — it doesn't recover dropped rows. | Partial ORDER BY (only col 1) + LIMIT. | `_ordered_sample_sql`: probes the column count (`LIMIT 0`, planned-not-executed) and does **ORDER BY ALL** columns (positional) → deterministic LIMIT. Applied to `sample_hash` + `sample_rows` on **both** engines. | live (PG sample_hash 2× → same hash) | 2026-06-25 |
| 8 | **q1** (TPC-DS, **MySQL**) — **executor ORDER: the #7 fix had no effect** | Even with #7, q1-MySQL kept being labeled `mechanics_failed` / `"optimizer produced no output"`. Cause: the executor computed `improvement_lower_bound_pct` (line ~456) **AFTER** calling `cost_gate_error` (line ~382). The gate received the `timing` **without** the lower-bound → saw `improvement_pct=None` → **vetoed** the valid rewrite (cost +3000×). Reflection then asked for a retry until the model **degraded to empty** (`"optimizer produced no output"`). #7 (the isolated gate) was correct but unreachable: the unit test passed because I fed it a `timing` that **already had** the lower-bound — it didn't exercise the real order. *(PG never fell into this: `cost_reduced=True` there → the veto branch doesn't even run; verified across the 4 PG timeout runs, all with cost DROPPING. Only MySQL flips the plan ranking — same root as cost≠time.)* | Move the floor/lower-bound computation to **BEFORE** the cost gate, in `timing`. **Retraction** guard: if S=1/SF1 later PROVES the rewrite wrong (`integrity_ok=False`), remove the lower-bound (no speed-up is reported for a wrong rewrite). | `test_executor_cost_gate_order.py` (2 **integration** cases driving `executor()`: approves with cost↑+timeout; rejects on EQUIVALENCE, never on cost, + retracts the lower-bound when SF1 fails) | 2026-06-22 |
| 16 | **q1/q7/q38/q69** (TPC-DS, **MySQL**, M1) — **FALSE-LAND from FLOOR-vs-FLOOR timeout** | In M1's 1st pass, q7/q38/q69 landed **3/3** and q1 **2/3** with `improvement_lower_bound_pct≈33.3%` and `optimized_ms≈30000` — **exactly** `OPTIMIZED_FAST_TIMEOUT_S`. PHANTOM gain: **the optimized version also timed out** (on a slow engine with no parallel query, the optimized version is ~15× slower than in PG and doesn't complete at full scale). | The lower-bound (`executor.py`) only checked `floor_original(45s) > optimized_ms(30s)` → computed `(45000−30000)/45000 = 33.3%`. But `optimized_ms=30000` was the **optimized version's timeout floor**, not a measurement → compared **floor-against-floor** (45s of the original vs. 30s of the optimized = a constant artifact of the two budgets). The gain-evidence guard (#prior) didn't catch it: the poisoned lower-bound made `has_gain_evidence=True`. | Lower-bound only when the optimized version **COMPLETED** (`and not optimized_timed_out`). Optimized version timed out → **no** lower-bound → falls to the **SF1-perf fallback**, which measures BOTH sides at SF1 (`perf_scale="SF1"`) → lands only with REAL gain, or becomes `no_gain`. *(PG never fell into this: the optimized version COMPLETES at full scale → real time; only MySQL times out on both sides.)* | `test_optimized_timeout_floor_is_NOT_a_lower_bound` + `test_optimized_timeout_floor_no_sf1_is_NOT_a_land` (integration via `executor()`, real order) | 2026-07-15 |
| 17 | **q30** (TPC-DS, **MySQL**, M1) — **FALSE-ACCEPT via `scale_divergence` (cross-scale override)** + new EQUIVALENCE class | q30 landed at 83.4% via `scale_divergence=True`: full-scale gave S≠1, but SF1 matched (66=66) → the system **inferred** "the original misbehaves at volume" and **accepted it**. Except **PG rejected** that same q30 (equiv-fail 3/3, where the original COMPLETES at SF20). Investigation: the original is **intractable at SF20** (>278s); the decorrelation is exact under exact arithmetic — the only divergence is **non-associative float** in the `> avg(ctr_total_return)*1.2` comparison (summation order changes the last digit → boundary rows enter/leave). SF1 is **too small** to exercise this → it masked a REAL scale-dependent non-equivalence. **q81 (legitimate) and q30 (false) are IDENTICAL to the pipeline** (match at SF1, diverge at SF20) — the override accepted both by inference. | `scale_divergence` **inferred** that the original was the anomalous one without ever **observing** it; and SF1 has no authority over scale-dependent divergence. Also **q1 is q30's twin** (same `>avg*1.2`, same SF1-only check on MySQL), indistinguishable in a single engine. | **(1) Retires the override:** SF1 only adjudicates when full-scale **has no baseline** (timeout, `integrity_ok is None`); a **DEFINITIVE S≠1** (two hashes, differ) **stands** (rejects), SF1 does not overturn it. One authority per run = the scale that completes. **(2) Provisional tag** `float_sensitive_sf1_only`: a land whose equivalence came **only from SF1** AND the predicate is `> avg/sum` → marks scale-fragility (not a gate — the land stays, but flagged for cross-engine/full-scale adjudication; q1 confirms, q30 fails). Detector `is_float_threshold_comparison` (dialect-agnostic). Scale ladder (SF5/SF10) was **discarded** (loading cost). | `test_fullscale_definitive_mismatch_not_overturned_by_sf1` + `test_float_threshold_sf1_only_land_is_tagged_provisional` + `test_nonfloat_sf1_only_land_is_not_tagged` (integration via `executor()`) | 2026-07-16 |
| 16b | **q1** (TPC-DS, **MySQL**, M2) — **FLOOR-vs-FLOOR via ANOTHER route (#16 was incomplete)** | The land with `lb=33.33%` and `optimized_ms=30001` (=`OPTIMIZED_FAST_TIMEOUT`) reappeared, **despite #16**. Cause: #16 checked `optimized_timed_out`, which comes from **SAMPLE_HASH** (LIMIT 100); but `optimized_ms` (which becomes the lower-bound) comes from **EXPLAIN ANALYZE** (full execution). The two time out INDEPENDENTLY — on MySQL the optimized version's sample_hash PASSED (`optimized_timed_out=False`) while its EXPLAIN ANALYZE TIMED OUT (floor 30000) → #16's guard didn't catch it → floor-vs-floor again. | `not optimized_timed_out` (the sample's signal) does **not** guarantee that EXPLAIN ANALYZE completed. | Require that the optimized version's EXPLAIN ANALYZE has **COMPLETED**: `opt_ms < opt_timeout*1000 − 500` (below its OWN budget). Floor → no lb → falls to SF1-perf. | `test_opt_explain_floor_with_ok_sample_is_not_a_lower_bound` (integration `executor()`) | 2026-07-16 |

| 18 | **THE WHOLE matrix** (PG + MySQL) — **INVERTED ATTRIBUTION of failure in re-strategizing** (not a gate bug: it's a FEEDBACK bug, and it contaminates the main claim) | During re-strategizing, the architect always received `prev_why="could not be written"` whenever there were `reflection_errors` — **including when the failure was EQUIVALENCE (S≠1), which is the STRATEGY's fault**. That is: the architect was told that **the CODER couldn't write it**, when in fact the coder wrote it faithfully and the result diverged. It would then switch technique thinking the problem was the writer. Discovered while auditing `adherence` on the 12 `equivalence_failed` of M3-A (MySQL): **12/12 `adherent`**, and several strategies literally prescribed the transformation that broke it (q30/q81 *"Filter customer_address Early in CTE"*; q63/q85 *"Split the OR condition"*). | [architect.py:229](../src/pipeline/nodes/architect.py#L229) collapsed the classes: `("could not be written" if reflection_errors else …)`. The correct signal **always existed** — the executor writes `"Integrity check failed … (S≠1)"` into `reflection_errors` (and semantic failure **routes to the architect**, not to the corrector) — it was just flattened on the way back. | Classify from the blob: S≠1/hashes-differ/count-differs → *"your strategy WAS written and DID RUN, but the result set is not the same — the chosen transformation doesn't preserve equivalence"*; syntax/execution → keeps `"could not be written"`. **Behind toggle `TRUE_FAILURE_FEEDBACK` (label `+truefb`), OFF by default** — turning it on unconditionally would make every future run incomparable with the matrix already run. **ANTI-SEEDING:** reports the FACT established by S=1, never the fix (test locks the absence of a technique name). | `test_bug18_*` (4): default preserves legacy behavior · S≠1 tells the truth · syntax stays "unwritable" · feedback doesn't name a technique | 2026-07-19 |

> **THREAT TO THE VALIDITY of the results ALREADY PUBLISHED internally (consequence of #18):** the entire existing matrix (PG complete + MySQL M1/M2/M3/M3-A) was produced with the inverted attribution. Hence **the light models' FIND may be UNDERESTIMATED** — part of the `no_gain`/technique-switching during re-strategizing may be a reaction to false feedback, not a capability limit. **Practical consequence:** the claim *"the 8B's FIND is the wall → future work = increase the finder"* **cannot be closed** before the `+truefb` A/B. This is an execution-order matter, not an invalidation: the land-rates remain valid (S=1 never depended on `prev_why`); what remains pending is the **attribution of the cause**.

| 19 | **L8 · `raw · ds-llama · TPC-DS/PG`** — **PARTIAL CELL REPORTED AS COMPLETE** (ORCHESTRATION bug, not a run-judgment bug) | At 01:00 on 02/09 the peak window closed and the supervisor killed `run_matrix` to hand the machine over to the cloud. `p1_local.sh` nonetheless logged `DONE (exit 0)`, the queue wrote the `.DONE` and moved on to W1 — with the cell at **6 of 21 queries** (18 runs, reach 0). **Two independent causes:** (a) `log "... DONE (exit $?)"` read the `$?` from a previous `grep`, never from `run_matrix` — the exit code was never propagated on the local side; (b) `run_matrix`'s `stdout` was buffered because it went to a file, so killing the process took the buffer with it: **4h24 of execution left a 16-line log**, with no per-run line and no `== done:`. The only existing guard looked for `"== done: 0 ok, N failed"` — since there was no `== done:` line at all, it passed silently. **Fix:** `_RC=$?` captured immediately + a new guard "**no `== done:` in the log means run_matrix did NOT finish**" (exit 4) in `p1_local.sh` and `p1_cloud.sh`, and `PY` now runs `python -u`. L8's `.DONE` was deleted; `--resume` picks up the 15 remaining queries. |

| 20 | **C3 · `monolithic · flash · TPC-DS/PG`** — **CONTAMINATED CORPUS: the cell measured a different set of queries from its pair** | `control_monolithic.sh` scanned `queries/heldout_op2/` — the "fresh" set from the P2 era — because **q28** was on that phase's query list. P1's corpus is 21 queries and q28 is not one of them. Result: C3's store accumulated **q28 (5 runs) and q88 (5 runs)**, and what looked like "20 of 21 queries" was actually **18 from the corpus + 2 strays**. C2×C3 — the DECOMPOSITION number — was being computed between **different query sets**. The two intruders landed, so they inflated the cell: removing them, C3's reach drops from **16 to 14** and the consistent count from **11 to 9**. **Fix:** `DIRS` without `heldout_op2` (verified beforehand: none of the 21 live only there); the 11 records moved to `.cache/_quarentena_p2_fora_corpus/` (**moved, not deleted** — they are valid measurements, just from another corpus); and `cell_status.py` now distinguishes **MIXTURE** (part of the corpus + part from outside — the dangerous case) from **OTHER CORPUS** (a whole cell from a different set, like the fresh batch — it's not residue, do not move). |

| 21 | **SL1 and `p1_llama_raw`** — **MIXED CONFIG WITHIN THE CELL** (the 3rd form of the accounting family) | Each cell should measure ONE configuration, and two didn't. **SL1** (`qwen aided-local WITH schema`, 115 records) had **1 run without `+schemalink`** — the cell's first one, from 29/08, before `SL=on` took effect. **`p1_llama_raw`** (64 records) had **1 run without `noplan`**, i.e., with the execution plan ON in a cell whose regime is plan-OFF. **In both cases the count did not give it away:** "102 runs, 21 queries" and "64 runs, 21 queries" looked healthy. And the damage is targeted at the very point the cell exists for — the run without schema was precisely in the cell that exists to measure the schema effect. **Fix:** `cell_status.py` now reports the distinct `(model, writer)` combinations and flags the MINORITY ones; the 2 records went to `.cache/_quarentena_regime_misto/` (moved, not deleted). No reach changed — both were `mechanics_failed` — but the cells now measure one thing only. |

## Common pattern (what to learn)
- The bugs come from **Phase A proxies** (parser/AST guard), not from the equivalence gate.
- **S=1** is the authority — when in doubt, **let it decide** instead of pre-rejecting. **#6 raised this from rule to ARCHITECTURE:** the structural guard no longer renders a verdict (it was an inversion of authority — a cheap proxy overriding the strong jury); it only **advises** (hint on rejection, caveat on approval). Any Phase A proxy from here on must be advisory, never blocking.
- **S=1 is not FORMAL proof** (it's empirical verification): sample-hash `LIMIT 100` + **EXACT cardinality** (actual rows from EXPLAIN ANALYZE — catches count divergence beyond the sample) + SF1 instance **only on timeout/S≠1**. Honest limit = **instance-specific** (equivalent on this data ≠ proof for all). Goes into threats to validity.
- **q38 is the textbook case:** it caught TWO distinct bugs (Cartesian-parser on INTERSECT in jun/14; guard constant-fold in jun/20). Neither was a model limit.
- **New sub-class (#11–#15, 2026-06-25): ENGINE ASYMMETRY.** Unlike #1–#10 (Phase-A false positives), these are cases where **MySQL measures with DIFFERENT rigor from PG** — weaker S=1 (#11), timing that can turn into wall-clock (#12), underestimated cost (#13), PG index guidance leaking into the MySQL prompt (#14) — plus one non-determinism common to both (#15). Any **PG-vs-MySQL contrast** (one of the paper's central axes) would be **a machinery artifact** without these fixes. **Found by a PRE-CAMPAIGN AUDIT** (before running MySQL), not post-hoc → re-run cost avoided. **Lesson:** when the system is cross-engine, audit **measurement parity between engines** BEFORE the campaign; PG working doesn't guarantee MySQL. Re-validate on a known query per engine before releasing the MySQL campaign.
- **Constant-fold has already bitten TWO guards** (#1 numeric, #5 filter): whenever a guard compares literals/expressions **by text**, it needs to **fold the arithmetic** first — the model tends to write `1999`, not `1998 + 1`. Suspect any guard doing textual threshold matching.
- **#7 is a different family — veto by COST, not Phase-A.** Lesson: **NO gate may veto on cost if real TIME improved** — and "time improved" includes the timeout's **lower-bound** (not just `improvement_pct`). It's the very cost≠time thesis applied to our own code (we almost gated on cost). **The silent timeout (improvement_pct=None) is the recurring trap** (bit the cost_gate #7 and the cost_vs_time_table): any logic reading `improvement_pct` must have a fallback to `improvement_lower_bound_pct`.
- **#8 is the most expensive lesson: fixing the FUNCTION isn't enough if the ORDER in the node is wrong.** #7 fixed `cost_gate_error` and the unit test passed — but the real bug was that the executor only filled in the lower-bound **after** calling the gate. **The isolated test gave false confidence**: it could only pass a `timing` that *already had* the lower-bound, so it didn't exercise the real order. New rule: **a bug living in a function called by a node must have a test at the NODE level** (a real `executor()`, in the real call order), not just the function in isolation — otherwise the regression comes back without warning. #8's diagnostic symptom: `outcome=mechanics_failed` with `errors=["optimizer produced no output"]` BUT `metrics` with `hash_match=true` and a positive `improvement_lower_bound_pct` → a good attempt was vetoed and the model degraded to empty during reflection.
- **Why these bugs (#7/#8) only show up in MySQL:** MySQL's cost model **inverts the ranking** of correlated-vs-decorrelated (underestimates the O(N²), overestimates the single pass) → `cost_reduced=False` → enters the veto branch. PostgreSQL ranks correctly (cost drops) → `cost_reduced=True` → the branch never runs. The same planner weakness that generates cost≠time is also what exposes our gating bugs — internal confirmation of the thesis.
- **#9 repeats #8's lesson in another form: the right fix at one call-site doesn't cover the others.** `_reasoning_flag` was wired into the architect (23/06) but **index_advisor was left behind** (24/06) — "silent config" (unset flag → engine default) doesn't error, it just **corrupts the data's label** (the record says `+think`, the index ran `nothink`). Rule: when introducing a control for an experimental variable (reasoning, engine, prompt), **grep ALL `get_llm()`/call-sites** and wire them all at once; a forgotten node becomes a hard-to-notice mislabel (it doesn't break, it just biases).
- **Data implication of #9:** the index records labeled `qwen-reasoning+think` have their **index generated nothink** → when reporting the reasoning axis **for the index**, those records are invalid (regenerate after the fix, if the index enters the on/off contrast).
- **#16 is the timeout's silent 3rd bite — now from BOTH sides.** #7/#8 dealt with "original timed out → timeout lower-bound"; #16 is the case where the **OPTIMIZED version also times out** and its `execution_time_ms` becomes the **timeout value** (floor), not a measurement. Comparing two floors (`(floor_orig − floor_opt)/floor_orig`) gives a **phantom constant** (here `(45−30)/45 = 33.3%`, identical across the 3 runs = red flag). **Reinforced rule:** an `execution_time_ms` only becomes a gain if that side **COMPLETED** (`not timed_out`); a timeout floor never feeds a gain — not as a point-estimate, nor as a lower-bound. Diagnostic: `improvement_lower_bound_pct` **identical** across all runs + `optimized_ms ≈ OPTIMIZED_FAST_TIMEOUT_S` → it's floor-vs-floor, not a land. **Only shows up on a slow engine (MySQL):** on PG the optimized version completes → real time; that's why the SF1-perf fallback is the honest safety net for MySQL.
- **#16b is the lesson of the WRONG completeness proxy:** #16 used `optimized_timed_out` (the **sample_hash**'s signal) as a proxy for "the optimized version completed", but `optimized_ms` comes from **EXPLAIN ANALYZE** — a DIFFERENT source, which times out independently. **Rule:** the completeness guard must check the SAME source as the metric (did EXPLAIN ANALYZE complete? → `opt_ms < its_budget`), never a correlated signal from another measurement. Sample-hash-ok ≠ explain-analyze-ok on a slow engine.
- **#17 is the 1st EQUIVALENCE one (not performance): "don't attribute an error to the original that you didn't observe."** `scale_divergence` accepted by **inference** ("SF1 matches → the original is the one that broke at volume") without ever **seeing** the original break — and SF1 is too small to catch **scale-dependent** divergence (boundary float in `>avg/sum`). Double lesson: (i) when two hypotheses are **indistinguishable** to the machinery (q81-legitimate ≡ q30-false: both match at SF1 and diverge at SF20), **don't accept by inference** — downgrade to `unverified` or treat the completing scale as the sole authority; (ii) a false-ACCEPT (equivalence) is worse than a false-reject in a measurement paper → prefer the conservative option. **Cross-engine is the gold-standard judge** (PG runs the original at volume and exposes it), but that's a bench luxury, not a production mechanism → hence the `float_sensitive_sf1_only` **tag** instead of a blind accept/reject.
- **#21 closes the ACCOUNTING family, and the pattern across the three is the same: the total keeps looking healthy.** #19 gets the corpus's SIZE wrong, #20 gets its IDENTITY wrong, #21 gets its internal HOMOGENEITY wrong. None of the three produces an error, message, or odd count — that's why all three survived repeated checks. **The defense that worked in all three was the same: compare what's on disk with what the cell CLAIMS to be**, instead of checking whether the numbers "make sense".
- **#21 was found by a QUESTION, not by an audit.** The user asked *"who is finding in SL1 and W1?"* — I had looked at SL1 several times that same day comparing cells AGAINST EACH OTHER, and never compared the labels WITHIN one. Worth noting as method: the simplest questions about the setup (*who finds? who writes? on which database?*) find contamination that no aggregate metric shows.
- **#20 is #19 one level down: #19 gets the corpus's SIZE wrong, #20 gets its IDENTITY wrong.** A partial cell at least announces itself (queries are missing); a contaminated cell has a FULL count and looks healthy — 100 runs, 20 queries. The damage is worse because it survives review: whoever looks at "20 queries, n=5" considers it closed. **The right question is never "how many queries does the cell have", it's "ARE THEY THE SAME ones as the cell I'm going to compare it to".**
- **Corollary: a query directory is state, and old state leaks.** `heldout_op2` stayed in `DIRS` for a reason that expired (reaching q28, from the P2 list) and nobody revisited it when the corpus changed. Every time a paper's corpus is redefined, campaign `DIRS` must be revisited too — the list of queries and the list of directories are the SAME decision written in two places.
- **Lesson from the FIX, not the bug (02/09): "cleaning up residue" nearly became a bigger mess than the residue.** The first quarantine attempt swept the whole `suggestions_*` and moved **269** records, emptying out `heldout_op2_pg`, `system_flash_fresh`, `system_pro_fresh` and `op2c_hint` — stores whose PURPOSE is exactly those queries. There was no loss only because the operation was a `move`, not a `delete`, and everything could be returned. **Rules that remain: (a) store cleanup runs with EXPLICIT TARGETS, never a broad glob; (b) move, never delete; (c) "outside the P1 corpus" ≠ "trash" — it depends on the cell.**
- **#19 opens the 3rd family: CAMPAIGN ACCOUNTING bug.** #1–#17 misjudge *one run*, #18 misfeeds *the loop* — #19 doesn't get any run wrong: the 18 from L8 are correct. The error is claiming that the **cell** finished. It's the same damage as the `cell_status` counting bug (25/08, `p1_qwen_raw` reported with 89 runs while having 62): **a panel documented as closed over a partial corpus**. And it's the 2nd time the lying `exit 0` bites — `p1_cloud.sh` got the same fix on 28/08 and I didn't carry it over to the local side, repeating to the letter #9's lesson ("the right fix at one call-site doesn't cover the others").
- **Operational corollary: a buffered log is a log that vanishes.** A process killed midway doesn't flush. Every long chain redirected to a file runs `python -u` — otherwise the evidence of what happened dies with the process, exactly when it's most needed.
- **#18 opens a NEW family: FEEDBACK bug (not a gate bug).** #1–#17 are *judgment* errors — the system accepts/rejects wrongly, and the damage shows in the outcome. #18 gets no outcome wrong: S=1 stays correct, the land-rates stay valid. It gets the **information returned to the agent** wrong, and the damage shows up in **attribution** (we thought the light model couldn't handle it, when part of that was it reacting to false feedback). **Lesson:** audit not just *"is the verdict correct?"* but *"does the message sent back to the agent describe the verdict that actually happened?"* — an agentic loop silently degrades when the feedback lies, and no outcome test catches it. **Diagnostic symptom:** `adherence=adherent` en masse in the `equivalence_failed` cases (the writer obeyed ⇒ the error was born in what was ASKED, not in what was written) — that's how it surfaced.
- **Method corollary:** before turning an observed limit into a capability claim (*"the small model can't do X"*), check whether **the machinery didn't sabotage the model** — it's the same rule as [[feedback_verify_model_limit_vs_bug]], now extended from the parser/guard to the **feedback loop**.
- Whenever a new bug is discovered: **fix it + add a regression test + record it here**.

---

## #22 — `SIGSTOP` does not preserve ownership of the `.env`: a frozen cell wakes up with ANOTHER cell's config

**When:** 10-11/09/2026 · **Cost:** a whole night of local window + an unplanned US$1.44
**Symptom:** the `L4` leg declared `exit 0` with **2 runs** in its own store, while **25 records**
showed up in `suggestions_p1cloud_llama_flash` — the CLOUD cell, which had already closed.

### The sequence

| time | event |
|---|---|
| 10/09 06:13 | `p1_local.sh` (L4) writes the local `.env` · **matrix guard checks out** · canary passes |
| 10/09 07:00 | cloud window → `peak_local.sh` **FREEZES** (`SIGSTOP`) L4's `run_matrix` |
| 10/09 11:21 | the cloud chain starts P1-1a and **overwrites the `.env`** with its own config |
| 10/09 22:00 | L4 **unfreezes** and runs for 4h51 — the server reads the `.env` **at request time**, and the `.env` is now P1-1a's |
| 11/09 04:40 | L4 exits `exit 0`; P1-1a went from n=3 to n=5 with nobody asking for it |

### Root cause

`SIGSTOP` stops the **process**, but the `.env` is **shared global state**, and the server rereads it on
every request. The matrix guard runs **once, before** the freeze — and there is **no re-check on
unfreezing**. ⇒ Freezing walks right under the very guard that exists to prevent this.

**The defect was SYMMETRIC:** `peak_local.sh` also froze the CLOUD cell whenever the peak returned,
with the same risk in the opposite direction.

### What did NOT happen (audited)

**No data was contaminated.** The 115 entries in `p1cloud_llama_flash` have a **single
signature** (`llama+nothink+nohw+noplan` / `cloud:deepseek-v4-flash` / aided / postgres). The 42 extra
runs were under the **correct regime for that cell** — the `.env` was in fact its own. The damage was
one of **work attribution**, not data validity.
Positive side-effect: P1-1a became n=5 (105 runs, reach 12, **8 consistent**) and became
comparable with C1 without truncation.

### Fix

**`peak_local.sh`: yielding the window now means KILLING, not freezing** — in both directions. On
return, `perna()` relaunches the leg, `p1_local.sh` **rewrites the `.env`**, and the matrix guard runs
again: ownership is re-established on every resumption.

**Why freezing existed, and why the reason died.** The original comment said *"freeze,
never kill — killing makes the leg exit != 0 and the chain treats it as complete"* (bug #19, the L8 at 6 of 21).
That stopped holding in two steps: (a) `p1_local.sh`/`p1_cloud.sh` gained the `== done:` guard
(exits 4 if `run_matrix` didn't reach the end); (b) the queues' `perna()` treats `rc=4/143` as an
**interruption**, relaunching **without consuming an attempt**. Killing became safe — and
`offpeak.sh` had already been doing this on the cloud side all along. The asymmetry between the two
supervisors was the bug.

**Bonus:** it also kills death-by-socket for a run frozen more than ~40 min (measured 2x: 07/09 and
08/09), because there is no longer any frozen run.

### Lesson

**Freezing a process does not freeze the state it shares.** Any guard that validates configuration
needs to run at the point where the configuration is **used**, not just where it's written — or the process
needs to be restarted so it rewrites it.

---

## #23 — the "empty cell" guard confused **FINISHED** with **BROKE**

**When:** 13/09/2026 · **Cost:** ~15 min of window + a log record that LIES about L4
**Symptom:** the local queue's `L4` leg failed **3 times with `rc=2`**, used up all its attempts, and
was recorded as *"giving up after 3 real failures"* — **with the cell already COMPLETE.**

### What the log actually said

```
== done: 0 ok, 0 failed, 20 queries skipped (resume), 1 CAPPED by the attempt ceiling ==
NO run was measured — exiting with code 2
```

20 queries at 5/5 + `q11` capped at n=4 = **104 runs, 21/21 queries**. There was **nothing**
left to run. The failure was instant: the matrix guard checked at 22:10:34 and `rc=2` came at 22:10:35.

### Root cause

The condition was `if ok == 0 and (fail or capped): return 2`.

**`capped` is not a failure.** A query that hits the attempt ceiling is a **legitimate completion** — it's
the ceiling working as designed (see `RUN_ATTEMPT_FACTOR`, no design bug). What it requires is
**reporting with reduced n**, not an error code.

The guard was born on 28/08 for a real and opposite case: C4 ran with the MySQL containers stopped, 63
runs returned HTTP 500, and the EMPTY cell was marked complete. The rule was right for that
case and wrong for this one — it didn't distinguish **"nothing ran because it broke"** from **"nothing ran because it
finished"**.

### Fix

`scripts/run_matrix.py`: `if ok == 0 and fail:` — only a real failure (an attempted run that broke) justifies a
hard failure. `skipped` and `capped` are completion states.

### The damage that nearly slipped through

The lost time is the least important part. The serious part is that the log recorded **"3 real failures"** for a
cell that closed successfully. A future audit — or myself, two weeks from now — would read this
and conclude L4 didn't close. ⇒ **A false error message is worse than lost time: it contaminates
the record.**

### Lesson

**Every guard that detects "empty" needs to distinguish empty-because-of-error from empty-because-of-completion.** It's the
same family as bug #19 (partial cell reported as complete), just in the reverse direction: there a
false success hid a failure; here a false failure hid a success.

---

## #24 — the supervisor relaunched a **finishing** cell FOREVER, silently

**When:** 14/09/2026 · **Cost:** ~10 min for this occurrence, but the defect was a **total stall** —
the chain would **never** advance to the next leg on its own while the cloud queue was empty.
**Symptom:** after a reboot, the chain relaunched leg `L4` (already complete) and the supervisor's log
recorded *"filling the idle window with local work"* **twice in 4 minutes**. No `run_matrix` alive,
no output anywhere, and leg `L5` never started.

### Root cause — two defects in the SAME branch

`peak_local.sh` has **two paths** for launching a cell:

| branch | how it launched | did it check the exit code? |
|---|---|---|
| **peak** (22:00-01:00, 03:00-07:00) | `setsid "$@" >>"$LOG.cmd" 2>&1 &` | yes, at the end of the loop |
| **idle window** (no cloud queue) | `"$@" &` | **NO** |

**(1) Invisible output.** The idle branch didn't redirect. Since the supervisor runs under
`nohup >/dev/null`, everything the cell printed was discarded. The cell died within seconds,
repeatedly, and **there was nowhere to look** — I had to run `p1_local.sh` by hand to find the
cause. Same family as **bug #19**: invisible execution.

**(2) Infinite relaunch loop — the serious one.** The branch did `"$@" & ; sleep 120 ; continue`,
**never examining the exit code**. The completion check (`wait $CHILD` → *"free cell
COMPLETED"* → `exit`) only exists at the end of the loop, reachable only via the PEAK branch.
⇒ On the idle window, a cell that **FINISHES SUCCESSFULLY** was relaunched every 2 minutes,
indefinitely, and the chain **never reached the next leg**.

### Why it only showed up on 14/09

It needed **two simultaneous conditions**:

1. **the cloud queue emptying out** (10/09, when coverage closed) — only then did the idle branch become the
   normal path, instead of the exception;
2. **the cell finishing quickly** — L4 closed on 13/09, so `p1_local.sh` started exiting in ~1 s.

With a long-running cell the defect stayed hidden: the child was still alive at 120 s, `kill -0` passed, and the
branch just went back to sleep. **A total-stall bug that needed 4 days and two state changes
to manifest.**

### Fix

`scripts/campanhas/peak_local.sh`, idle branch: now redirects like the peak branch
(`setsid "$@" >>"$LOG.cmd" 2>&1 &`) **and** handles completion the same way — if the child exited, the
supervisor exits with the **same code**, and the chain's `perna()` decides (0 = leg fulfilled, continue;
≠ 0 = attempt or interruption).

**Verified in practice:** relaunched at 14:57, the chain recognized L4 as complete at 14:59 and
advanced on its own to **L5**.

### Lesson

**Every path that launches work needs the SAME guarantees.** This branch was an incomplete copy
of the other one — no logging and no exit-code check — and the difference stayed invisible while the
rare branch hadn't become the common one. ⇒ When two paths exist for the same thing, either they
share code, or the divergence becomes a bug waiting for a condition.

**Related:** this is the third defect in a row in the same family — **#19** (invisible execution
/ false completion), **#23** (false failure hiding success), and now **#24** (success ignored
turning into a loop). All are born from a **misread or unread exit code**.

---

## #25 — the BENCHMARK suffix landed AFTER `STORE=`: IMDb runs written into a CLOSED TPC-DS cell

**When:** 16/09/2026 · **Cost:** ~3h30 of window + **20 records** written to the wrong cell
**Severity:** the highest so far — contaminated a **CLOSED AND DOCUMENTED** cell (`L0c`).
**Symptom:** the `I1` leg (IMDb/PostgreSQL raw) ran with `DB_URI` correctly pointing to
IMDb, but `SUGGESTION_STORE_DIR` was `suggestions_p1_qwen_aided_local_raw` — **L0c's**
store, **TPC-DS**. The **INDEX** store, meanwhile, was correct (`index_..._imdb_raw`).
**It was this asymmetry that gave the bug away.**

### Root cause

In `scripts/campanhas/p1_local.sh`, the order was:

| line | what |
|---|---|
| 82 | `STORE=".cache/suggestions_p1_${FAMILY}_aided_local${SUF}"` |
| 168 | `STORE=` (again) |
| **196** | **`SUF="${SUF}_imdb"`** ← too late for both `STORE=` assignments |
| 226 | `IDXSTORE=...${SUF}` ← in time, which is why the index came out right |

**This is an EXACT RECURRENCE of a rule the file itself carries.** Line 71 has said, since
29/08: *"**RULE: everything that changes SUF comes before any STORE=**"* — written after the exact
same accident with `schema-linking`, when SL1 started writing into L4's store. The `BENCH` block is from
**26/08**, three days BEFORE the rule, and nobody moved it to obey it.

**Why it had never blown up before:** the `BENCH=imdb` path inside `p1_local.sh` had **never
been used**. All the project's IMDb cells came from `p1_cloud.sh` or `imdb_battery.sh`.
It had existed for 21 days, broken, waiting for its first use.

### There were TWO waves, not one

| wave | records | cause |
|---|---|---|
| 1st (04:40-08:05) | **19** | the suffix bug |
| 2nd (08:10:05) | **1** | **server window × `.env`** — see below |

**The second wave hit AFTER the fix.** `p1_local.sh` writes the `.env` and only then restarts the
server; but the server **reads the `.env` at startup**, and for the first ~55 s of the leg it was still the
old process, with the previous configuration in memory. The leg's first run went to the
old store. From the restart onward, the following 12 went to the correct store.
**This window is a RESIDUAL failure the fix does not cover** — narrow, but real, and on a benchmark
switch it contaminates another cell. **Recorded, not fixed.**

### Fix

`p1_local.sh`: the assignment `[ "${BENCH:-tpcds}" = "imdb" ] && SUF="${SUF}_imdb"` was moved to
**before the first `STORE=`**, alongside the other assignments that change `SUF`. The `BENCH` block now only keeps
the URIs, the containers, and the query directories.

**Dry-tested on both paths** before re-enabling:
`BENCH=imdb` → `suggestions_p1_qwen_aided_local_imdb_raw` · without `BENCH` → `..._aided_local_raw`.

### Containment

1. Chain stopped as soon as the live matrix was checked.
2. **20 records moved** to `.cache/_quarentena_imdb_no_store_tpcds/` — **MOVED, NEVER
   DELETED** (the lesson from the earlier quarantine incident, which globbed `suggestions_*` and gutted
   fresh stores). Selected by a **double condition**: IMDb content (`movie_info`/`cast_info`/…) **AND**
   absence of TPC-DS tables — never by date alone.
3. **L0c restored and checked against the doc:** 102 runs · 21 queries · reach 7 · 3 consistent.

### Lesson

**A rule written in the file doesn't protect the code that was already there.** The 29/08 rule was created
for `SL`, applied to `SL`, and nobody swept the file for other spots that violated it — `BENCH`
was 130 lines away, in the same file, breaking it.
⇒ **When writing a rule because of a bug, sweep the whole file for the other cases.**

And the problem's detector was no guard at all: it was the **asymmetry between two fields that should
agree** (suggestion store × index store). The matrix guard checked `family` and `store` and
**passed** — it doesn't know that `DB_URI` is from another benchmark.
**Obvious improvement for the matrix guard:** check that `DB_URI`'s benchmark matches the store's
suffix. Not implemented.
