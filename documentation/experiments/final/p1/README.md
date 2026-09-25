# P1 — cell GUIDE (local regime: no plan, no masking)

> **This file is the GUIDE for this folder**: the naming convention, how to read a cell doc, the index
> of the 22 cells, and the panel's current state.
> The campaign diary (audits, queues, and dated decisions) was split off into
> [`HISTORY.md`](HISTORY.md) on 13/09 — it is **a record, not state**.

## HOW THE FILES ARE NAMED — convention fixed 02/09

```
<CODE>__<dataset>-<engine>__<arm>__<who-finds>-<who-writes>[__<axis>].md
```

**Why it changed.** THREE schemes coexisted (`C1_…`, `p1_<model>_<arm>`, `tpcds_mysql_<arm>`) and
**none carried the five coordinates** that identify a cell. The case that forced the change:

**`tpcds_mysql_aided_cloud.md` said neither the model NOR the writer** — and the writer there was
`deepseek-chat`, which the **CP1** probe proved (02/09) is not equivalent to `flash`: they agree on 2 of 5
queries, diverging both ways. **Had the writer been in the name, those 60 runs would have been
flagged months earlier.** Today the file is called
`X-AC__tpcds-mysql__aidedcloud__qwen-CHAT-MORTO.md` and the problem reads right off the `ls`.

| field | values | note |
|---|---|---|
| **CODE** | `C1` `C2` `C3` `C4` `SL1` `L0` `L0b` `M2b` `W1` `L8`… | the queue label, when one exists — it's how we talk about the cell |
| | `RAW` `AL` `AC` | cell **without** a queue label: the role (raw · aided-local · aided-cloud) |
| | `X-` (prefix) | **retired/superseded** cell — do not use in a new number |
| | `SYNTHESIS` | synthesis doc, not a cell |
| **dataset-engine** | `tpcds-pg` `tpcds-mysql` `imdb-pg` `imdb-mysql` | the 4 combinations |
| **arm** | `raw` `aidedlocal` `aidedcloud` `ceiling` `mono` | |
| **finds-writes** | `qwen-flash` `llama-qwencoder` `flash-flash` | in `raw` and in `mono` there's only one model, so only one name |
| **axis** | `noplan` `planON` `hintB` `schemalink` | only when it distinguishes two cells that are otherwise identical |

**`raw` × `mono` are not the same thing**, even though both have 1 agent: `raw` is a **local**
model alone; `mono` is a **cloud** model alone (the skeptic's control). The stamp inside C3 still says
`ceiling` because the generator classifies by the `writer` field, which for a single agent comes empty — **the file
name is the correct source**, the stamp is a known quirk.

**`tpcds_mysql.md` and `tpcds_mysql_raw_noplan.md` were the SAME cell** (raw · MySQL · qwen · n=3),
distinguished only by `PLAN_HINTS`. Now that's in the name: `…__planON` × `…__noplan`.


## INDEX of `final/p1` — **every cell doc opens with a stamp** `dataset · engine · arm · n`. Four combinations coexist (TPC-DS/PG · TPC-DS/MySQL · IMDb/PG · IMDb/MySQL); never compare rows with different stamps without declaring it.


> Docs are **generated** by `scripts/gen_cell_doc.py <store> --out <file>`. Regenerate instead of editing.
> The narrative (conclusion at the top, threats to validity) is human-written and goes above the warning line.

## FULL INDEX — the 5 coordinates of each doc

> **Only the P1 regime lives here.** `noplan` · no masking · no rules. **SL1** and **LA** are the
> deliberate exceptions (schema is their point) and form the cross-engine pair for the local arm.
> **HOMOGENEOUS** cells — one `(model, writer)` combination per cell (`fixed_bugs.md` #21).
> `dataset × engine` matrix COMPLETE: C1 · C4 · R1 · I-MY.
> ENGINE axis on both arms: local SL1×LA · cloud C1×C4.
> **aided-cloud** column cross-model **CLOSED with 4 models**: C1 (qwen, n=5) · P1-1b (mistral) · P1-1c (deepseek-r1) · P1-1a (llama, n=5). `ds-llama` left out by decision.
> **THE INVERSE-GAIN LAW — 4 models, no inversion** (n-matched pairs):
>     mistral **1→16** (+15) · llama **3→12** (+9) · qwen **7→13** (+6) · deepseek-r1 **7→12** (+5).
>     ⇒ the worse the finder alone, the bigger the rescue by the cloud writer.
> Local WRITER axis: SL1 (qwen2.5-coder, n=5) · W1 (deepseek-coder, n=3) · W2 (qwen3+think) incomplete.
> The three writers are at **different n** — the axis does NOT yet close as a comparison.

| file | dataset | engine | arm | FIND | WRITE | size |
|---|---|---|---|---|---|---|
| [`AL__tpcds-pg__aidedlocal__llama-qwencoder.md`](AL__tpcds-pg__aidedlocal__llama-qwencoder.md) | TPC-DS | PG | `aided-local` | llama | qwen2.5-coder:7b | 105r n=5 |
| [`AL__tpcds-pg__aidedlocal__mistral-qwencoder.md`](AL__tpcds-pg__aidedlocal__mistral-qwencoder.md) | TPC-DS | PG | `aided-local` | mistral | qwen2.5-coder:7b | 63r n=3 |
| [`C1__tpcds-pg__aidedcloud__qwen-flash.md`](C1__tpcds-pg__aidedcloud__qwen-flash.md) | TPC-DS | PG | `aided-cloud` | qwen-reasoning | deepseek-v4-flash | 108r n=5 |
| [`C2__tpcds-pg__ceiling__flash-flash.md`](C2__tpcds-pg__ceiling__flash-flash.md) | TPC-DS | PG | `aided-cloud` | qwen-reasoning | deepseek-v4-flash | 105r n=5 |
| [`C3__tpcds-pg__mono__flash.md`](C3__tpcds-pg__mono__flash.md) | TPC-DS | PG | `mono` | qwen-reasoning | — (1 agent) | 105r n=5 |
| [`C4__tpcds-mysql__aidedcloud__qwen-flash.md`](C4__tpcds-mysql__aidedcloud__qwen-flash.md) | TPC-DS | MySQL | `aided-cloud` | qwen-reasoning | deepseek-v4-flash | 52r n=3 |
| [`I-MY__imdb-mysql__aidedcloud__qwen-flash.md`](I-MY__imdb-mysql__aidedcloud__qwen-flash.md) | IMDb | MySQL | `aided-cloud` | qwen-reasoning | deepseek-v4-flash | 39r n=3 |
| [`L0__tpcds-pg__raw__deepseek.md`](L0__tpcds-pg__raw__deepseek.md) | TPC-DS | PG | `raw` | deepseek | — (1 agent) | 63r n=3 |
| [`L4__tpcds-pg__aidedlocal__qwen-qwencoder__schemaOFF.md`](L4__tpcds-pg__aidedlocal__qwen-qwencoder__schemaOFF.md) | TPC-DS | PG | `aided-local` | qwen-reasoning | qwen2.5-coder:7b | 104r n=5 |
| [`L5__tpcds-pg__aidedlocal__deepseekr1-qwencoder.md`](L5__tpcds-pg__aidedlocal__deepseekr1-qwencoder.md) | TPC-DS | PG | `aided-local` | deepseek-r1 | qwen2.5-coder:7b | 110r n=5 |
| [`I1__imdb-pg__raw__qwen.md`](I1__imdb-pg__raw__qwen.md) | IMDb | PG | `raw` | qwen-reasoning | — (1 agent) | 39r n=3 |
| [`I2__imdb-mysql__raw__qwen.md`](I2__imdb-mysql__raw__qwen.md) | IMDb | MySQL | `raw` | qwen-reasoning | — (1 agent) | 39r n=3 |
| [`L0b__tpcds-pg__raw__qwen.md`](L0b__tpcds-pg__raw__qwen.md) | TPC-DS | PG | `raw` | qwen-reasoning | — (1 agent) | 102r n=5 |
| [`L8__tpcds-pg__raw__dsllama.md`](L8__tpcds-pg__raw__dsllama.md) | TPC-DS | PG | `raw` | deepseek-llama | — (1 agent) | 63r n=3 |
| [`LA__tpcds-mysql__aidedlocal__qwen-qwencoder.md`](LA__tpcds-mysql__aidedlocal__qwen-qwencoder.md) | TPC-DS | MySQL | `aided-local` | qwen-reasoning | qwen2.5-coder:7b | 67r n=3 |
| [`M2b__tpcds-mysql__raw__qwen__noplan.md`](M2b__tpcds-mysql__raw__qwen__noplan.md) | TPC-DS | MySQL | `raw` | qwen-reasoning | — (1 agent) | 48r n=3 |
| [`P1-1a__tpcds-pg__aidedcloud__llama-flash.md`](P1-1a__tpcds-pg__aidedcloud__llama-flash.md) | TPC-DS | PG | `aided-cloud` | llama | deepseek-v4-flash | 105r n=5 |
| [`P1-1b__tpcds-pg__aidedcloud__mistral-flash.md`](P1-1b__tpcds-pg__aidedcloud__mistral-flash.md) | TPC-DS | PG | `aided-cloud` | mistral | deepseek-v4-flash | 66r n=3 |
| [`P1-1c__tpcds-pg__aidedcloud__deepseekr1-flash.md`](P1-1c__tpcds-pg__aidedcloud__deepseekr1-flash.md) | TPC-DS | PG | `aided-cloud` | deepseek-r1 | deepseek-v4-flash | 63r n=3 |
| [`R1__imdb-pg__aidedcloud__qwen-flash.md`](R1__imdb-pg__aidedcloud__qwen-flash.md) | IMDb | PG | `aided-cloud` | qwen-reasoning | deepseek-v4-flash | 39r n=3 |
| [`RAW__tpcds-pg__raw__llama.md`](RAW__tpcds-pg__raw__llama.md) | TPC-DS | PG | `raw` | llama | — (1 agent) | 64r n=3 |
| [`RAW__tpcds-pg__raw__mistral.md`](RAW__tpcds-pg__raw__mistral.md) | TPC-DS | PG | `raw` | mistral | — (1 agent) | 63r n=3 |
| [`SYNTHESIS__raw_axis__tpcds-pg.md`](SYNTHESIS__raw_axis__tpcds-pg.md) | — | — | `SYNTHESIS` | — | — | — |
| [`SYNTHESIS__semantic_wall__tpcds-mysql.md`](SYNTHESIS__semantic_wall__tpcds-mysql.md) | — | — | `SYNTHESIS` | — | — | — |
| [`SL1__tpcds-pg__aidedlocal__qwen-qwencoder.md`](SL1__tpcds-pg__aidedlocal__qwen-qwencoder.md) | TPC-DS | PG | `aided-local` | qwen-reasoning | qwen2.5-coder:7b | 106r n=5 |
| [`W1__tpcds-pg__aidedlocal__qwen-dscoder.md`](W1__tpcds-pg__aidedlocal__qwen-dscoder.md) | TPC-DS | PG | `aided-local` | qwen-reasoning | deepseek-coder:6.7b | 63r n=3 |

**Generated from the docs themselves on 02/09** — if it diverges from the file name, the doc is the source.
The `X-AC__…CHAT-MORTO` row is here as a RECORD, not as a usable result.


## HOW TO READ A CELL DOC

Each doc has **two halves**, separated by a warning line:

```
<HUMAN narrative — conclusion, finding, threats to validity>
<!-- GENERATED BELOW — do not edit; regenerate with scripts/gen_cell_doc.py -->
<tables GENERATED from the store — never typed by hand>
```

**Never edit below the marker.** To update:
`.venv/bin/python scripts/gen_cell_doc.py <store> --out <file>` — the narrative above is preserved
(the generator cuts at the marker) and the tables are redone from the store. Writing the narrative **below**
the marker makes the generator discard it on the next regeneration — it has already happened.

### The outcomes — what each one means

| outcome | what it is |
|---|---|
| `rewrite_correct` | **LAND** — rewrite valid at the S=1 gate **and with gain**. This is what counts as success |
| `no_gain` | **valid** rewrite, zero gain. FIND found it, WRITE wrote it correctly, the planner flattened it. It's the **class-d** — proof that *reach ≠ FIND working* |
| `mechanics_failed` | the SQL **doesn't run**. Always read the breakdown by CAUSE (see below) |
| `equivalence_failed_semantic` | the SQL **runs** but returns a different result — S≠1. Wrote well, answered a different question |
| `equivalence_failed_structural` | rejected by the structural guard (e.g., reverted to the original) |
| `timeout` | the **rewrite** did not finish within budget |
| `unverified` | the **original** did not complete, so S=1 could not be evaluated. Not an approval |

### `mechanics_failed` should NEVER be read as a single block

`cell_status.py` breaks it down by cause, and the distinction **changes the conclusion**:

| cause | what it means |
|---|---|
| `nonexistent source` | column/table that doesn't exist — **hallucination**, genuine writing failure |
| `syntax` | malformed SQL — writing failure |
| `execution timeout` | **VALID SQL** that doesn't finish within budget. **NOT a writing failure** and cannot be added up as one |

It was this breakdown that saved the reading of **C4**: 69% of its `mech_f` was execution timeout,
not a writing error. Without separating them, the conclusion would have been the opposite.

### "Baseline timeout" is not a run

When the **original query** does not finish within budget, the attempt dies before the pipeline runs:
no strategy, no rewrite, nothing to count. **It does not enter any denominator**, and `--resume`
ignores it. The docs report these numbers **separately** from the runs — add them in and every rate becomes wrong.

### Consistency and the attempt ceiling

**Consistency = ≥3 lands out of 5 runs.** The ruler **only exists at n=5** — in an n=3 cell it doesn't
apply, and `cell_status` writes `consistency N/A`.

**Attempt ceiling** (`scripts/run_matrix.py:203`): `max(runs+1, int(runs × RUN_ATTEMPT_FACTOR))`,
with factor **2** (not in `.env`; the default holds). ⇒ **6** attempts at n=3, **10** at n=5, counting
real runs **+** baseline timeouts. A query that hits the ceiling stops with **reduced n**, and the cell's doc
**has to say which query and at what n** — never present it as if it had full n.

### The three rules that hurt the most when forgotten

1. **Never compare reach across different n.** Truncate with `--first-n 3` for panel reading.
2. **Never compare rows with different stamps** (dataset/engine) without declaring it.
3. **Check homogeneity before citing.** `cell_status.py` warns `MIXED CONFIG` when a store
   holds more than one `(model, writer)` combination — that was **bug #21**. A mixed store requires a filter
   (`--model-contains` / `--model-excludes`) **declared in the doc**.



## PANEL STATE — 13/09/2026

> Generated from the stores, not from memory. When it changes, **update it here** — it was the
> decay of this section that forced the split into [`HISTORY.md`](HISTORY.md).

| column | models | state |
|---|---|---|
| **raw** (1 local agent) | **5/5** | complete cross-model panel, n=3 — `r1` **7** · `qwen` **6** · `llama` **3** · `ds-llama` **1** · `mistral` **1** |
| **raw · benchmark × engine quadrant** | **4/4** | **CLOSED 16/09** — TPC-DS/PG **29%** · TPC-DS/MySQL **24%** · IMDb/PG **62%** · IMDb/MySQL **31%** |
| **aided-cloud** (writer `flash`) | **4** | **CLOSED** — `qwen`/C1 **13** (n=5) · `mistral`/P1-1b **16** · `r1`/P1-1c **12** · `llama`/P1-1a **12** (n=5). `ds-llama` left out by decision |
| **aided-local** (local writer) | **4** | **CLOSED, all without schema** — `llama` **4** · `qwen`/L4 **3** · `r1`/L5 **2** · `mistral` **1**. (`qwen`/SL1 **3** is the same with schema ON) |
| **ceiling / monolithic** | — | C2 **17** = C3 **17** (n=5) |
| **2nd engine** | MySQL | C4 **8**/17 · LA **3** · M2b **4** · I-MY **12**/13 |

### THE WRITER LAW — what decides is its LEVEL, not decomposing

Pairs by model, byte-identical FIND, only the writer changes:

| model | raw | **+ LOCAL writer** | **+ CLOUD writer** |
|---|---|---|---|
| **mistral** | 1/21 | **1** (0) | **16** (**+15**) |
| **llama** | 3/21 | **4** (+1) | **12** (**+9**) |
| **qwen** | 7/21 | **3** (**−4**) | **13** (+6) |
| **deepseek-r1** | 7/21 | **2** (**−5**) | **12** (+5) |

With a **CLOUD** writer everyone gains — more the weaker the finder.
With a **LOCAL** writer the weak ones tie and the **STRONG ONES LOSE** — more the better they are.

⇒ **Decomposing is not an architectural improvement: it's an ALLOCATION mechanism.** It only pays off when it lets you put
someone better in each role. Separating while keeping the same level **is not neutral — it's a loss.**
`C2 × C3` gives the **null** case (same strong model in both roles: 17 = 17); the local arm gives the
**negative** case. `n` caveat: only the **qwen** pair is fully matched n=5 × n=5.

### THE RAW QUADRANT — benchmark × engine interaction (16/09)

| | queries | reach | rate | `mech_f` |
|---|---|---|---|---|
| TPC-DS / PostgreSQL | 21 | 6 | 29% | 29% |
| TPC-DS / MySQL | 21 | 5 | 24% | 34% |
| **IMDb / PostgreSQL** | 13 | **8** | **62%** | **15%** |
| IMDb / MySQL | 13 | 4 | 31% | 28% |

**The IMDb jump exists ONLY in PostgreSQL** — it isn't "the JOB is easier", it's an **interaction**. The
`mech_f` is the throughline (15% → 34%), rising every time MySQL enters.
**With only I1 closed the reading written down was *"the model delivers double on IMDb"* — and it was wrong.**
Third case in the campaign where a partial gave the opposite conclusion from the complete data.

### What's still open

| | cell | missing |
|---|---|---|
| 1/5 | **L4** · aided-local qwen **without schema** | ~9 runs — closes the `L0c × L4` pair (local decomposition) **and** the schema axis `SL1 × L4` |
| 2/5 | **L5** · aided-local of `deepseek-r1` | 95 runs |
| 3/5 | **M2b** · TPC-DS/MySQL raw | ~14 runs |
| 4/5 | **W2** · writer `qwen3:8b +think` | ~38 runs |
| 5/5 | **LB** · reasoning OFF | 105 runs — if it doesn't close by 7/oct, claim (iv) **is dropped** |

**Live queue:** [`scripts/campanhas/fila_local_v4.sh`](../../../scripts/campanhas/fila_local_v4.sh).
Resume after reboot: `bash scripts/campanhas/resume_all.sh`.
**Cloud: nothing pending** — coverage closed on 10/09.


## The `n` rule, decided cell by cell (19/08)

| n | where | why |
|---|---|---|
| **5** | **qwen** ladder · **contour law** pairs | headline = count comparison between arms |
| **3** | cross-model panel · IMDb · **P1-0** | coverage, or **component selection** |

**P1-0 stays at n=3 by explicit decision:** it's a *component selection* cell (which local writer),
not the headline. Raising it would cost ~11 days of calendar time without changing the claim. **State it in the methodology.**

## INDEX NEGATIVE CONTROL (P1-6) — run 19/08, zero cost

On the clean anchor (`final_workload_pro`, 21 queries, n=5, one writer, one window), out of the **10 queries where
the rewrite did not land**, the model proposed an index in **10**, but the estimated benefit exceeds 1% in
only **4**. In 6 of them there are 20-65 recommendations with **0.0%** benefit.

**An aggregate of 29 cells gave "45% helped" — a number that must NOT be used.** It sums two
engines, four writers, with and without rules and different eras. `scripts/index_negative_control.py` now
**refuses to aggregate** and requires a single cell. And the benefit today is **100% estimated** (11,785 estimated ×
22 measured across the whole corpus) — the planner overestimates.

**Reading:** in the queries where the rewrite doesn't solve it, in most cases **there is no opportunity at all** —
neither logical nor physical. It matches the CEILING (the strong FIND unlocked 1 of 4) and LITHE, whose failures
are *structural simplicity* and *structural tightness*.


## TRAPS THAT HAVE ALREADY COST US DAYS — read before citing a number

| trap | what happened |
|---|---|
| **counting a file ≠ counting a run** | `cell_status` was adding `timeout` as a run in a **raw** cell (raw has no `writer`, and the rule was `writer or arm=="raw"`). `p1_deepseek_raw` stayed **a whole month** documented as closed while having **50 runs and 3 queries at ZERO** — 50+13 timeouts = 63, which matched the target. Fixed 25/08: rule identical to `_done_counts`' |
| **store name doesn't say what the cell is** | `p1_qwen_aided_local_raw` is **raw** · `deepseek-r1:8b` is distilled on top of **Qwen3** (`ollama show`) · `tpcds_mysql` holds **two regimes** in the same directory |
| **regime label is a prefix of another** | `+nohw` matches inside `+nohw+noplan` — filtering by substring merges the arms. Use `--model-excludes` |
| **queued queue ≠ documented queue** | bit us 3× in one day. `health_check.sh` #5 checks item by item |
| **completion marker without exit code** | C2 aborted via the canary on 22/08 (`exit=1`) and the chain recorded "COMPLETE"; the queue below it proceeded. Fixed 26/08 |
