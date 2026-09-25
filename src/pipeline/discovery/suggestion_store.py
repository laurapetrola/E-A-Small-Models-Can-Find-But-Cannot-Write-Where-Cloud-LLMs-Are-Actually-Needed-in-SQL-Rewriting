import hashlib
import json
import os
from datetime import datetime

# raw (unaided) = default `.cache/suggestions` (intacto). O pipeline AIDED (find-tips + coder) seta
# SUGGESTION_STORE_DIR (ex.: .cache/suggestions_aided) → braços separados, sem mistura. RUN_ARM rotula o record.
STORE_DIR = os.getenv("SUGGESTION_STORE_DIR", ".cache/suggestions")
# Rolling-buffer cap (aplicado a suggestions E index). ERA 500 → evictava dados de MEDIÇÃO: rodar as
# rodadas de MySQL (5×63) empurrou pra fora a célula qwen/PG (63→1 record). Num paper de medição cada run
# é dado — subido pra 100k (efetivamente sem eviction; JSONs são pequenos, a matriz toda ~10-15k). Env-tunável.
# ⚠️ NÃO recupera o que já foi apagado (só previne perda daqui pra frente); os raw_21q.md condensados são o registro.
MAX_RECORDS = int(os.getenv("SUGGESTION_STORE_MAX_RECORDS", "100000"))


def _query_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()[:12]


def save_run(
    suggestions: list[str],
    improvement_pct: float | None,
    raw_sql: str,
    model: str = "",
    outcome: str | None = None,
    used_fallback: bool = False,
    inference_ms: float | None = None,
    optimized_sql: str | None = None,
    rules_in_prompt: list[str] | None = None,
    db_type: str = "",
    corrector_rescued: bool = False,
    scale_divergence: bool = False,
    float_sensitive_sf1_only: bool = False,
    production_path: str | None = None,
    reached_sql: str | None = None,
    reasoning_trace: str | None = None,
    metrics: dict | None = None,
    errors: list[str] | None = None,
    strategy: str | None = None,
    writer: str | None = None,
    strategy_attempts: int | None = None,
    adherence: dict | None = None,
) -> None:
    # Record a run if it carries either a discoverable intent (LLM suggestions / free-form
    # technique labels) or a known outcome. Rejected free-form attempts used to be lost here:
    # their `suggestions` is empty, so Direction B->A never saw the "right idea, broken SQL"
    # cases. With the free-form labeler now running on rejected attempts too, those carry a
    # label; `outcome` additionally lets the discoverer separate "model reached for it and
    # landed it" from "reached for it but the mechanics failed".
    if not suggestions and not outcome:
        return
    os.makedirs(STORE_DIR, exist_ok=True)
    record = {
        "timestamp": datetime.now().isoformat(),
        "query_hash": _query_hash(raw_sql),
        "suggestions": suggestions,
        "improvement_pct": improvement_pct,
        "model": model,
        "arm": os.getenv("RUN_ARM", "raw"),  # BRAÇO: raw (unaided) vs aided (find-tips + coder). Record sem o campo = raw (legado).
        "outcome": outcome,
        "used_fallback": used_fallback,
        "inference_ms": inference_ms,  # TOTAL LLM inference for the run (generation + index advisor + labeler)
        "rules_in_prompt": rules_in_prompt or [],  # PROVENANCE: learned rules injected (arch B); [] = unaided discovery
        "apply_mode": bool(rules_in_prompt),  # was this an APPLICATION-mode (rule-guided) run vs neutral discovery?
        "db_type": db_type or None,  # ENGINE tag: the same query shares a query_hash across PG/MySQL — this separates them
        "scale_divergence": scale_divergence,  # RETIRED accept path — always False now (kept for schema back-compat)
        "float_sensitive_sf1_only": float_sensitive_sf1_only,  # PROVISIONAL land: S=1 only on SF1 + `>avg/sum`
        # threshold → scale-fragile equivalence (full-scale unconfirmed); cross-engine/full-scale adjudicates. #17
        "production_path": production_path,  # PROVENANCE: how the SQL was produced —
        # detector:<transform> (system wrote it, NO LLM) | free_form (LLM wrote it) |
        # free_form_corrected:<local|cloud> (LLM wrote it, coder repaired it) | no_change. Makes every
        # record state WHICH method reached the answer (Pilar 2 detector vs Pilar 1 free-form vs assisted).
        "corrector_rescued": corrector_rescued,  # PATH: True = a class-(b) attempt the corrector mechanically
        # repaired into an approved rewrite (model REACHED the technique, coder cleaned the WRITE). Separates
        # "model alone (a)" from "model + mechanical assist (b→a)" — the FIND capability signal stays
        # corrector-independent (whether the model reached it), distinct from the WRITE outcome.
        "optimized_sql": optimized_sql,  # the VALID rewrite (only on rewrite_correct) — worked example for human formalization
        # reached_sql: the model's BEST attempt that reached the technique — the VALID rewrite when it landed,
        # else the furthest-developed BOTCHED attempt that at least PARSES (a syntax-garbage attempt → None).
        # This is the EVIDENCE of a reach: the teachable gate (degrau 1) uses it to tell a genuine reach
        # (deepseek's FILTER consolidation that botched) from a weak model's HALLUCINATED label on broken SQL.
        "reached_sql": reached_sql,
        # The model's chain-of-thought (<think>) — EVIDENCE the FIND is reasoned, not guessed; kept for
        # qualitative analysis of HOW reasoning models reach a technique. Capped to bound store size.
        "reasoning_trace": (reasoning_trace[:20000] if reasoning_trace else None),
        # PROOF of where it ERRS / where it LANDS, per run:
        #   metrics — full block (same as the manual curl): execution_time (original_ms/optimized_ms/
        #     improvement_pct/lower_bound/floor), planner_cost, integrity (hash_match=S=1), inference
        #     breakdown, reflection_iterations (the FIND trigger: 0=proactive, ≥1=needed feedback).
        #   errors  — the rejection reasons WHEN it erred (syntax/Cartesian/non-equivalence/timeout); [] when it landed.
        "metrics": metrics,
        "errors": errors or [],
        # 2-AGENTES: a ESTRATÉGIA NL do architect (o FIND) — gravada mesmo no b→b → permite replay offline
        # (cloud-writer na MESMA estratégia). `writer` = QUEM escreveu o SQL aprovado (local:<m>|cloud:<m>).
        "strategy": strategy,
        "writer": writer,
        # 2-AGENTES: quantas ESTRATÉGIAS DIFERENTES o architect tentou (o Loop 2 = re-estratégia no no-gain).
        # 1 = um passe; >1 = re-estrategiou N-1× antes de landar/esgotar→índice. O store era CEGO a isso (o
        # stream mostrava, o record não) → sem esse campo o engajamento do Loop 2 é não-medível.
        "strategy_attempts": strategy_attempts,
        # ADERÊNCIA (WRITER_ADHERENCE_CHECK): o writer aplicou a técnica da estratégia do architect? juiz LLM
        # local independente — {verdict: adherent|deviated|unclear}. Protege a atribuição FIND-qwen/WRITE-cloud.
        "adherence": adherence,
        # The ORIGINAL is kept whenever there is a reach to compare against (landed OR botched), so the
        # before→after pair exists for the formalizer AND _genuine_reach can diff reached_sql vs raw_sql.
        "raw_sql": raw_sql if (optimized_sql or reached_sql) else None,
    }
    filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f") + ".json"
    with open(os.path.join(STORE_DIR, filename), "w") as f:
        json.dump(record, f, indent=2)
    # Keep only the most recent MAX_RECORDS files
    existing = sorted(
        (os.path.join(STORE_DIR, fn) for fn in os.listdir(STORE_DIR) if fn.endswith(".json")),
        key=os.path.getmtime,
    )
    for old in existing[:-MAX_RECORDS]:
        os.remove(old)


STORE_DIR_INDEX = os.getenv("INDEX_SUGGESTION_STORE_DIR", ".cache/index_suggestions")  # mesmo esquema raw/aided


def save_index_run(
    recommendations: list[dict],
    raw_sql: str,
    model: str = "",
    db_type: str = "",
    rewrite_hints: list[str] | None = None,
    reasoning_trace: str | None = None,
    inference_ms: float | None = None,
    workload_aware: bool = False,
) -> None:
    """Persist the INDEX-axis discovery signal (EXPLORE mode only) — the index analog of save_run.
    Records WHICH index candidates the LLM recommended + the SIMULATED outcome (benefit, helped?,
    workload impact) + the MODEL (so we can see what a weak model recommends vs a strong one — the
    Pilar-1-for-indexes baseline) + the causal `rewrite_hints` (the "why", e.g. LIKE→GIN). This is the
    corpus a future DETERMINISTIC index-candidate generator ('Pilar 2 for indexes') will be mined from:
    which (table/column/index_type) recurrently HELP. Empty list → no-op."""
    if not recommendations:
        return
    os.makedirs(STORE_DIR_INDEX, exist_ok=True)
    record = {
        "timestamp": datetime.now().isoformat(),
        "query_hash": _query_hash(raw_sql),
        "model": model,
        "arm": os.getenv("RUN_ARM", "raw"),  # BRAÇO: raw vs aided (espelha save_run)
        "db_type": db_type or None,
        "rewrite_hints": rewrite_hints or [],  # causal "why" from the executor (e.g. "LIKE → GIN")
        "workload_aware": workload_aware,      # generation FORM: True = workload was in the prompt; False = per-query (target-only)
        "inference_ms": inference_ms,          # index-advisor LLM time (latency parity with the rewrite axis)
        "reasoning_trace": (reasoning_trace[:20000] if reasoning_trace else None),  # CoT: why these columns/types
        "recommendations": [
            {
                "table": r.get("table"),
                "column": r.get("column"),
                "index_type": r.get("index_type"),
                "estimated_benefit_pct": r.get("estimated_read_benefit_pct"),
                # HOW THIS INDEX WAS EVALUATED — the paper must state this per number, so it is
                # persisted per recommendation instead of being inferred from the run's config:
                #   "executed"  -> the index was really CREATEd inside a transaction and the query
                #                  EXECUTEd (warm-up discarded, median of 3), then rolled back
                #   "estimated" -> planner cost only; no execution ever happened
                # These fields were being DROPPED here: the backend computed them and this remapper
                # copies a FIXED field list, so they never reached the store (5th time this exact
                # pattern bites — computed in the node, lost at the store boundary). Found 18/08.
                "validation": r.get("validation", "estimated"),
                "measured_read_benefit_pct": r.get("measured_read_benefit_pct"),
                "baseline_exec_ms": r.get("baseline_exec_ms"),
                "optimized_exec_ms": r.get("optimized_exec_ms"),
                # HELPED = read benefit above the noise floor. Prefer the MEASURED number whenever it
                # exists: the whole point of executed validation is that the decision follows
                # execution, not the planner's estimate (which overestimated in 5 of 5 spot checks).
                "helped": (
                    (r.get("measured_read_benefit_pct") if r.get("measured_read_benefit_pct") is not None
                     else r.get("estimated_read_benefit_pct")) or 0.0
                ) > 1.0,
                "write_overhead_pct": r.get("write_overhead_pct"),
                "workload_aggregate_benefit_pct": (r.get("workload_impact") or {}).get("aggregate_benefit_pct"),
                # Per-query breakdown of the index's effect on the REST of the workload (some queries
                # improve, some REGRESS — the index trade-off). Was being dropped (only the aggregate
                # persisted) → restored so "index Y: q18 +25%, q40 −10%" is recoverable per record.
                "workload_per_query": (r.get("workload_impact") or {}).get("queries"),
                "workload_queries_improved": (r.get("workload_impact") or {}).get("queries_improved"),
                "workload_queries_regressed": (r.get("workload_impact") or {}).get("queries_regressed"),
                "status": r.get("status"),
            }
            for r in recommendations
        ],
    }
    filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f") + ".json"
    with open(os.path.join(STORE_DIR_INDEX, filename), "w") as f:
        json.dump(record, f, indent=2)
    existing = sorted(
        (os.path.join(STORE_DIR_INDEX, fn) for fn in os.listdir(STORE_DIR_INDEX) if fn.endswith(".json")),
        key=os.path.getmtime,
    )
    for old in existing[:-MAX_RECORDS]:
        os.remove(old)


def run_dates(store_dir: str | None = None, model_prefix: str | None = None) -> dict:
    """A DATA REAL em que uma célula rodou — min/max das `timestamp` dos registros (+ contagem). Pro
    cabeçalho dos docs (staleness à primeira vista: o doc MySQL de 11/jul suspeito teria sido óbvio).
    Filtra por prefixo de model se dado. Retorna {"first","last","n"} (datas YYYY-MM-DD) ou n=0."""
    d = store_dir or STORE_DIR
    ts: list[str] = []
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, fn)) as f:
                    r = json.load(f)
                if model_prefix and not (r.get("model", "").startswith(model_prefix)):
                    continue
                if r.get("timestamp"):
                    ts.append(r["timestamp"][:10])
            except Exception:
                pass
    if not ts:
        return {"first": None, "last": None, "n": 0}
    return {"first": min(ts), "last": max(ts), "n": len(ts)}


def load_all() -> list[dict]:
    if not os.path.exists(STORE_DIR):
        return []
    records = []
    for fn in sorted(os.listdir(STORE_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(STORE_DIR, fn)) as f:
                records.append(json.load(f))
        except Exception:
            pass
    return records
