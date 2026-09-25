# The SINGLE-AGENT axis (`raw`) — assembled · P1

## SYNTHESIS · `TPC-DS` · `POSTGRES` · **raw** axis (1 agent) · the panel's 4 models · n=3


> **Assembled on 2026-08-23** at the user's request. The per-cell docs already existed separately; what was
> missing was the **assembled** axis, with the configuration audit that says **which rows compare with which**.
> Per-cell docs: [`p1_qwen_aided_local_raw`](L0b__tpcds-pg__raw__qwen.md) *(qwen's — the name is misleading,
> it's raw)* · [`p1_llama_raw`](RAW__tpcds-pg__raw__llama.md) · [`p1_mistral_raw`](RAW__tpcds-pg__raw__mistral.md) ·
> [`p1_deepseek_raw`](L0__tpcds-pg__raw__deepseek.md) · [`tpcds_mysql`](RAW__tpcds-mysql__raw__qwen__planON.md).

## Why this axis exists

It is the **monolithic form of the published systems** (GenRewrite, LITHE) as measured by us. It serves two
purposes in the paper:

1. **Capacity floor** — the light model doing everything alone, with no scaffolding at all.
2. **The defense against *"you only tested YOUR architecture"*** — the existing form is measured, in
   both engines and in five model configurations.

## What "one agent" means (verified in the code, not in the naming)

In `raw` there is **one** call (`build_fallback_prompt`) and the SQL comes out of the **same response**
(`extract_query(content)`). In the 2-agent mode the architect returns a **natural-language strategy**
and `optimized_sql = None`; the writer is who writes.

**Both tasks — FIND and WRITE — happen in BOTH forms.** What changes is whether they're separated into
roles with an explicit interface, or **fused into a single inference**. In `raw` the strategy exists, but
it stays **implicit**: it never becomes an artifact, and so it's neither auditable nor attributable. Never write
that *"raw doesn't do FIND"*.

## The axis, cell by cell

`alc` = reach (landed in ≥1 run) · `mech` = mechanical failures (the SQL doesn't even execute) · `equiv-fail` =
rewrite ran but did not preserve the result · denominator = 21 queries, except where indicated.

> **No "consistent" column — on purpose (decision 23/08).** All these cells are **n=3**, and
> the consistency ruler **only exists at n=5**: the threshold of ≥3 lands is a majority of five, but at n=3 it becomes
> unanimity. These are **COVERAGE** cells, and the claim they support is **reach**.
> Lowering the threshold to "majority of n" was rejected — it would make "consistent" mean 2/3 in one cell
> and 3/5 in another, which is exactly the ambiguity the second ruler was created to eliminate.

| cell | model | engine | plan | runs | q | alc | mech | equiv-fail |
|---|---|---|---|---|---|---|---|---|
| `p1_deepseek_raw` | `deepseek-r1:8b` `+think` | PG | **OFF** | 63 | 21 | **6** | 13 | 5 |
| `p1_llama_raw` | `llama3.1:8b` `nothink` | PG | **OFF** | 63 | 21 | 3 | **42** | 8 |
| `p1_mistral_raw` | `mistral:7b` `nothink` | PG | **OFF** | 63 | 21 | 1 | **42** | 10 |
| `p1_qwen_aided_local_raw` | `qwen3:8b` `+think` | PG | **OFF** | 51 | **12** | 2 | 14 | 9 |
| `tpcds_mysql` (half A) | `qwen3:8b` `+think` | MySQL | **ON** | 63 | 21 | **8** | 27 | 5 |
| `tpcds_mysql` (half B) | `qwen3:8b` `+think` | MySQL | **OFF** | 63 | 21 | 4 | 12 | 9 |

## The plan audit — the answer is "almost"

**Question:** *is the plan off in none of them?* **Answer: in almost all, but there are three exceptions, and
one of them changes a number that has already been cited.**

- **PostgreSQL is clean.** All four PG cells are `noplan`.
  - A minimal impurity: `p1_llama_raw` has **1 run** (out of 64) with the plan on. Discard that run,
    not the cell.
- **`tpcds_mysql` has BOTH HALVES IN THE SAME STORE** — 63 plan-ON runs and 63 plan-OFF, same
  directory. It's necessary to filter by the label (`noplan`) before counting anything.
- **The number that has already circulated is in the WRONG arm.** The *"MySQL raw = 8/21"*, cited in §5b of
  the skeleton, is the **plan-ON** arm. Under the P1 regime (plan OFF) the reach is **4/21** — half.
- **The monolithic control (`control_monolithic_flash`) does NOT belong to this axis.** Label:
  `qwen-reasoning+nothink+nohw+schemalink+cloudfind` — plan ON, schema-linking ON, `nothink`, and the FIND
  comes from the cloud. It's something else; see the fix in §5c of the skeleton.

## What's missing from the axis

| gap | why it matters | where it stands |
|---|---|---|
| **`qwen` raw `nothink` in PG** | without it **RQ3** (*does reasoning help?*) **doesn't close** — only the `+think` leg exists, and the `nothink` cells in PG carry `+schemalink`, so they don't pair up | **L3b** in the local queue · 63 runs · free |
| `qwen` raw `+think` in PG, to complete | 12 of the 21 queries | **L3** · 29 runs · free |
| `MySQL aided-cloud` plan-OFF | without it there's no **pair under the P1 regime** for MySQL — today the 8×7 pair is entirely plan-ON | not in the queue · costs cloud |

## How to cite this axis in the paper

- **Can:** *"the monolithic form of the published systems was measured across 4 model families × 2 engines"* — with
  the table above, filtered by plan.
- **Can:** the `mech` contrast between families — `llama` and `mistral` fail mechanically in **42 of
  63** runs, against **13** for `deepseek` and **14** for `qwen`. It's the axis's cleanest signal: the weaker model
  doesn't get the strategy wrong, it **gets the SQL wrong**.
- **Cannot:** compare a plan-ON row with a plan-OFF row. They're different setups, and our own
  rule forbids it.
- **Cannot:** cite qwen-PG as if it had denominator 21 — it's **12 queries** until L3 closes.
