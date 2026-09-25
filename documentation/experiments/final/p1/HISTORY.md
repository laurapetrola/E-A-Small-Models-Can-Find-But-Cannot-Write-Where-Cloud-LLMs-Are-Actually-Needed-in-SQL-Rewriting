# HISTORY — `final/p1`

> **THIS IS A RECORD, NOT STATE.** Everything here describes the campaign **as it was on the date indicated**.
> The numbers, the queues, and the "in progress" items **no longer hold**. Nothing was deleted — the project already lost data
> from hasty cleanup, and "old" never means "trash" here.
>
> **For the CURRENT state, read the [`README.md`](README.md).**
>
> **Why this file exists (13/09/2026):** the README had turned into two things at once — a reading
> guide and a campaign diary. The diary part aged and started to induce error: it told readers to *"read
> first"* an audit from 19/08, announced the cloud as *"in progress"* after it had closed, and
> pointed the queue at `queue_p1_peak.sh`, a dead chain. It was the same defect the 11/09 inspection
> found in the `experimentation_article` docs.

## THE HEADLINE CELLS (cloud, in progress)

| cell | agents | FIND | WRITE | state *(26/08)* |
|---|---|---|---|---|
| [`C1`](C1__tpcds-pg__aidedcloud__qwen-flash.md) | 2 | `qwen` 8B local | `flash` | **CLOSED** — 108 runs · 21q · **reach 13** · 8 consistent |
| [`C2` — ceiling](C2__tpcds-pg__ceiling__flash-flash.md) | 2 | **`flash`** | `flash` | **CLOSED 28/08** — 104 runs · 21q · **reach 17** · **12 consistent** |
| [`C3` — monolithic](C3__tpcds-pg__mono__flash.md) | **1** | `flash` (implicit) | `flash` | **CLOSED 02/09** — 105 runs · 21q · reach **17** · consistent **12** · `mech_f` **3%** |

**C1 × C2** isolates *finder quality* · **C2 × C3** isolates *decomposition*.

> **The C2 × C3 pair CLOSED on 02/09 — and gave an EXACT TIE.** Same model, same regime, same corpus:
> reach **17 = 17**, consistent **12 = 12**, and the per-query trade is **1 × 1** (only the decomposed one lands
> q96; only the monolithic one lands q3). The monolithic one still writes better — `mechanics_failed` **3% × 10%**.
> Reading: **decomposing pays off where capacity is SCARCE (C1 × C2: 13 → 17); with a strong model, it
> doesn't buy reach.** Do not collapse the two lines — the 13 → 17 gain is from the MODEL, not from the
> decomposition. The C3 numbers prior to 02/09 (reach 16, `mech_f` 2%) were over a contaminated corpus
> (q28/q88, from the P2 era) and incomplete (q5/q50/q67 were missing) — see `fixed_bugs.md` **#20**. Three cells, two
> questions — because each pair changes **one** variable.

**Reasoning is CONSTANT across the ladder** — verified by API call on 25/08, not assumed: the qwen
8B reasons via the `+think` toggle; `deepseek-v4-flash` reasons **natively** (`reasoning_content`
present, `reasoning_tokens > 0`). That's what gives meaning to a C1×C2 tie: it means *"finding
wasn't the bottleneck"*, and **not** *"the ceiling was capped"*.
**Do NOT write** that the cloud FIND runs without reasoning: the campaign script's `REASONING`
variable is **inert** on that branch (`_find_llm` only passes the toggle on the local branch).

##### PARTIAL SIGNAL from C2 (8 of 21 queries — indication, NOT result)

| query | **C1** (qwen 8B finds) | **C2** (flash finds) |
|---|---|---|
| q1 · q7 · q69 | 5/5 · 5/5 · 5/5 | 5/5 · 4/4 · 5/5 — **tie** |
| **q38** | **0**/5 | **1**/5 |
| **q51** | **0**/8 | **3**/5 — and **consistent** |
| **q67** | **0**/5 | **1**/5 |

Where the 8B already lands, the ceiling **ties**. Where the 8B **never** lands, the ceiling finds a way.
**If the pattern holds across the 21, the headline needs to change shape**: it isn't *"the wall is WRITE"*, it's
**"the wall moves depending on the query"** — WRITE where the 8B reaches the strategy, FIND where it doesn't
reach it. At 8/21 and wins of 1/5, it can still dilute.

## MATRIX AUDIT — 19/08 (read first)

**8 of the 12 matrix cells exist.** But the audit found an underlying problem: **the model carrying the
HEADLINE had the dirtiest ladder in the panel.**

| | raw | aided-local | aided-cloud |
|---|---|---|---|
| **qwen** (headline) | didn't exist in TPC-DS/PG | n=3, `+think` | n=9, but `nothink` + writer from another era |
| **llama** (coverage) | n=3 | n=5 | n=5 |
| **mistral** | n=3 | n=3 | |
| **deepseek** | n=3 | | |

The two qwen rungs that existed used **different reasoning** (`+think` × `nothink`) and **writers
from different eras** — confounded across three axes. The llama, which is **coverage**, had the ladder
clean. **The opposite of what the paper needs.**

**Fix (in the peak queue, FREE):** `qwen raw` n=5 · `qwen aided-local` completed 3→5.
The qwen `aided-cloud` rung **already exists clean at n=5**: `6_fresh0804` (+think, v4-pro, 04/08).


## THE MySQL LADDER — closes the SINGLE-ENGINE exposure (brought in 21/08)

P1 measured **only PostgreSQL**, and that is a real exposure in an evaluation paper — the wall can be read
as a planner artifact. But the `qwen` MySQL ladder **already exists almost complete**:

| rung | cell | n | reach |
|---|---|---|---|
| **raw** (1 agent) | [`tpcds_mysql`](../p2/RAW__tpcds-mysql__raw__qwen__planON.md) | 6 | **8/21** |
| **aided-cloud** | [`tpcds_mysql_aided_cloud`](../p2/X-AC__tpcds-mysql__aidedcloud__qwen-CHAT-MORTO.md) | 3 | **7/20** |
| aided-local | **in the queue** (local, free) | 3 | — |

**The signal it brings is the INVERSE of PostgreSQL:** in MySQL **raw reaches 8** and aided-cloud
**7** — the single agent **ties or beats** the two-agent architecture. In PostgreSQL the relation is
reversed. If this is confirmed with the complete ladder, it's a strong finding: **the value of
decomposition depends on the engine**.

**Mandatory caveats when citing:** different writers (`None` × `deepseek-chat`, old era),
different `n` (6 × 3), and `tpcds_mysql` is `+think`. **Not a clean pair** — it's a signal, and it must
be stated as such. The missing cell (aided-local) runs under the current regime and is the one that gives a
reliable reading.

### And the characterization of the WALL by ORIGIN

[`SYNTHESIS__semantic_wall__tpcds-mysql.md`](SYNTHESIS__semantic_wall__tpcds-mysql.md) — brought over from `experiments/` on 21/08. Classifies
**deterministically** (DBMS error code + `precheck_semantic_preservation`, no LLM) the 53
runs that did not land in an aided-MySQL cell:

| origin | runs | % |
|---|---|---|
| **FIND** | **27** | **51%** |
| WRITE | 11 | 21% |
| execution (timeout) | 9 | 17% |
| loop (coder repeats) | 6 | 11% |

**In MySQL FIND is the wall; in PostgreSQL it's WRITE.** If this holds up, the answer to the
headline question is **conditional on the engine** — more specific and stronger than "the bottleneck is WRITE".

**The sentence this enables:** *"the wall reappears in a second engine for the model carrying the
headline"* — narrower than "we measured all models in both engines", and true.

## STATUS OF EACH CELL — read BEFORE citing any number

| cell | runs | n | writer | rules | window | status |
|---|---|---|---|---|---|---|
| [**`raw_axis`**](SYNTHESIS__raw_axis__tpcds-pg.md) — *the 1-agent axis ASSEMBLED + plan audit* | — | — | — | — | 23/08 | **start here** for any citation of raw |
| [`p1_deepseek_raw`](L0__tpcds-pg__raw__deepseek.md) | 63 | 3 | — (raw) | off | 30/07 | **valid** — cross-model panel |
| [**`p1_qwen_raw`**](L0b__tpcds-pg__raw__qwen.md) — *doc RENAMED 25/08; the store is still `p1_qwen_aided_local_raw`* | 102 | 5 | — (raw) | off | 19/08–10/09 | **CLOSED 10/09 (leg L0c)** — 21/21 queries, reach 7; `q81` n=2 and `q30` n=4 hit the attempt ceiling, **reduced n declared** |
| [`p1_llama_raw`](RAW__tpcds-pg__raw__llama.md) | 64 | 3-4 | — (raw) | off | 27-28/07 | **valid** — cross-model panel |
| [`p1_mistral_raw`](RAW__tpcds-pg__raw__mistral.md) | 63 | 3 | — (raw) | off | 28/07 | **valid** — cross-model panel |
| [`p1_llama_aided_local`](AL__tpcds-pg__aidedlocal__llama-qwencoder.md) | 105 | 5 | `qwen2.5-coder:7b` | off | — | **valid** — aided-local ladder |
| [`p1_mistral_aided_local`](AL__tpcds-pg__aidedlocal__mistral-qwencoder.md) | 63 | 3 | `qwen2.5-coder:7b` | off | — | **valid** — P1-0 arm 1 |
| [`p1_llama_aided_fresh0803`](../p2/AC__tpcds-pg__aidedcloud__llama-pro.md) | 105 | 5 | `v4-pro` | off | 03-08/08 | **pair for the contour law** (see below) |
| [`p1_llama_aided_hint_fresh0803`](../p2/AC__tpcds-pg__aidedcloud__llama-pro__hintB.md) | 105 | 5 | `v4-pro` | **on** | 03-09/08 | **pair for the contour law** (see below) |
| `p1_llama_aided` *(doc REMOVED 25/08 — superseded by `_fresh0803`; store at `.cache/suggestions_p1_llama_aided`, regenerable with `gen_cell_doc.py`)* | 63 | 3 | **not recorded** | off | 28/07 | **DO NOT USE** |
| `p1_llama_aided_hint` *(doc REMOVED 25/08 — superseded by `_fresh0803`; store at `.cache/suggestions_p1_llama_aided_hint`, regenerable with `gen_cell_doc.py`)* | 63 | 3 | `v4-pro` | on | 29-31/07 | **DO NOT USE as a pair** |

## Why two cells drop out of the picture (found 18/08)

**`p1_llama_aided` — the writer wasn't recorded.** The cell is from 28/07, prior to the `writer`
field's instrumentation. There's no way to know what wrote that SQL, so it cannot be cited as the
"aided-cloud" leg of any pair. This is what explains its reach of 3/21 against the 10/21 of
`fresh0803` with an apparently identical configuration: **they are not the same configuration**, and
the difference is not interpretable.

**`p1_llama_aided_hint` — loses its partner.** Its natural pair (no rules, same window, n=3) is
precisely the `p1_llama_aided` above. With no valid leg on the other side, the n=3 pair doesn't exist. It is
**replaced** by the n=5 pair below, which is contemporaneous and has a declared writer.

## The pair that SURVIVES — the contour law for `llama`

| | reach |
|---|---|
| `p1_llama_aided_fresh0803` (no rules) | **10**/21 |
| `p1_llama_aided_hint_fresh0803` (with rules) | **12**/21 |

Same window (03-08 and 03-09/08), same writer (`v4-pro`), same `n=5`. **This** is `llama`'s point
on the contour law — never the July pair.

## And all the `aided-cloud` cells above are SUPERSEDED for the cross-family comparison

The P1 headline compares **model families** (llama · mistral · deepseek · qwen). For that, the
writer and the window need to be the same across all of them — and the cells above use `v4-pro` across
different windows, spread over two weeks. That's why **P1-1** exists: redo the whole aided-cloud column
with `flash`, `n=3`, **in the same window**.

The July/August cells remain valid **within their own pair** (as is the case for the contour law
pair), but **never** across families.

## THE QUEUE — reordered by PRIORITY on 26/08

> **What changed and why.** The previous order put the rung that's **missing** in 10th position, behind
> 189 runs of writer selection. The user pointed out (*"I don't think we prioritized this well"*) and was right.
> Canonical source: [`scripts/campanhas/queue_p1_peak.sh`](../../../scripts/campanhas/queue_p1_peak.sh)
> (local, peak windows) and `queue_c1c2.sh` + `queue_p1_cloud_rest.sh` (cloud, off-peak).
> **Never simultaneous** — the exclusion guard kills one cell before bringing up the other.

### TIER 1 — the backbone. Without this there's no paper.

| | item | n | state |
|---|---|---|---|
| **L0** | close `p1_deepseek_raw` | 3 | 26/08 05:15 |
| **L0b** | close `p1_qwen_raw` (q81) | 3 | 26/08 05:16 |
| **L0c** | qwen raw n=3 → **n=5** | 5 | peak windows |
| **L4** | **qwen aided-local 21×5** | **5** | **the rung that's MISSING** |
| **C2 · C3** | ceiling · monolithic | 5 | · |

**Why L4 moved up to 2nd position.** The qwen ladder has `raw` (68 runs) and `aided-cloud`
(108 runs) closed, but **`aided-local` has 7 runs across 7 queries** — it practically doesn't exist. It
is the one that answers *"is the wall WRITE?"*, swapping who writes while keeping who finds.

**And P1-0 stopped blocking it.** It existed to ELECT the local writer — but the
aided-local column is **already standardized** on `qwen2.5-coder:7b` (llama 105 runs · mistral 63 · qwen 7).
Swapping the writer only for qwen would break the column, which is the same mistake as the three different
writers found in the cloud column on 25/08. → **The writer is chosen for column consistency**, and P1-0 became
an ablation (Tier 4). Honest phrasing: *"we use the writer that standardizes the column, and measure afterward whether the
choice matters"*.

### TIER 2 — IMDb raw: the GENERALITY filter for the rules

| | item | n |
|---|---|---|
| **I1** | IMDb/**PG** raw `noplan` | 3 |
| **I2** | IMDb/**MySQL** raw `noplan` | 3 |

**We have NO IMDb raw AT ALL under the P1 regime.** The three existing cells (`imdb_pg`, `imdb_mysql`,
`imdb_mysql_schemalink`) are **all plan-ON**.

**Why it's not "just a 2nd benchmark":** the last `discovery_yield` gave 7 rule candidates and **two
came with a TPC-DS column name glued inside** (`pre-filter CTE with ca_state = 'TN'`, `replace
IN subquery with JOIN to date_dim_filtered`). A rule distilled only from TPC-DS could be a **memorized
benchmark pattern**, not a rule. The test that separates the two: **does it survive on another dataset?** It's the
IMDb corpus that feeds **L6/L7** with rules that generalize.
And it fits the published critique (Leis et al., VLDB'15): TPC-DS is generated with the **same
assumptions** the optimizer makes.

**Two particularities:** in IMDb `VERIFY_DB_URI = DB_URI` (real dataset, no reduced scale —
**no land falls back to SF1**); and the containers stay **stopped** between campaigns.
Implemented as env `BENCH=imdb` in `p1_local.sh`. `run_matrix` picks the benchmark **by URI**
(`_benchmark_subdir`), so pointing to the right database is enough.
**The old item `M2` was REMOVED:** the label said "IMDb MySQL raw" but the command **did not have**
`BENCH=imdb` — it would have run **TPC-DS** under the IMDb name, and the result would have entered the wrong table.

### TIER 2b — conditional headline (engine axis, TPC-DS)

| | item | n |
|---|---|---|
| **M2b** | close TPC-DS MySQL raw `noplan` — 46/63, **5 queries at ZERO** | 3 |
| **LA** | qwen aided-local in MySQL | 3 |
| **C4** | qwen + `flash` in MySQL — redoes the aided-cloud column | 3 |
| **M3** | **llama** raw MySQL — 2nd model of the axis (scope B) | 3 |

The `tpcds_mysql_aided_cloud` cell uses writer **`deepseek-chat`** — renamed model, now returns
**400**. Writer from another era, not comparable with C1 → hence **C4**.
**Scope of the engine axis = B (qwen + llama).** Measured cost of one raw MySQL cell: **~18 h of
window** — 63 runs × ~330 s of inference (7 h) **+ ~17 timeouts × 40 min (11 h)**; timeouts
dominate. 5 models would cost ~13 days of peak and eat up the ladder. Mistral discarded for this
role: it would produce ~67% `mechanics_failed`, which doesn't discriminate.

### TIER 3 — cross-model panel

| | item | n |
|---|---|---|
| **P1-1 a/b/c** | llama · mistral · deepseek + `flash`, **same window** | 3 |
| **L5** | deepseek-r1 on FIND, aided-local | 3 |

### TIER 4 — ablations and secondary RQs *(nothing here blocks any rung)*

**LB** reasoning OFF · **L6/L7** contour law (mistral) · **L2b** discovery yield re-run (dry) ·
**L3b** raw NOTHINK (RQ3) · **L1b** positional-bias probe · **P1-0** ×3 (demoted to ablation)

---
