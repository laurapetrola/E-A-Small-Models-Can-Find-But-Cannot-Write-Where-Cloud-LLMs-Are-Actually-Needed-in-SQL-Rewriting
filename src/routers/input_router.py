import json
import os
import time
from datetime import datetime
import numpy as np
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from src.connections import get_embedding_model
from src.pipeline.graph import pipeline
from src.pipeline.cache.semantic_cache import SemanticCache
from src.pipeline.discovery.suggestion_store import save_run, save_index_run
from src.pipeline.discovery.freeform_labeler import label_freeform_techniques
from src.pipeline.backends import get_backend, get_db_type

PLANS_DIR = ".cache/plans"
MAX_PLANS_FILES = 50
SUGGESTION_SIMILARITY_THRESHOLD = 0.85


def _model_label() -> str:
    """Record label = MODEL_FAMILY + reasoning condition. The SAME model run with thinking ON vs OFF must
    be DISTINGUISHABLE in the store, else qwen-ON and qwen-OFF both save as 'qwen-reasoning' and collide
    (and collide with the pre-control data). `REASONING=on` → '<fam>+think', `off` → '<fam>+nothink',
    unset → bare '<fam>' (matches pre-2026-06-23 flag-unset runs). The reasoning axis is then just two
    distinct labels in characterization_table / gen_query_docs — no analysis change needed."""
    fam = os.getenv("MODEL_FAMILY", "")
    v = os.getenv("REASONING", "").strip().lower()
    if v in ("1", "true", "on", "yes"):
        label = f"{fam}+think"
    elif v in ("0", "false", "off", "no"):
        label = f"{fam}+nothink"
    else:
        label = fam
    # Rich-S≠1-feedback arm (WRITE-help) must be DISTINGUISHABLE from the plain-feedback baseline.
    if os.getenv("RICH_EQUIVALENCE_FEEDBACK", "").strip().lower() in ("1", "true", "on", "yes"):
        label += "+richfb"
    # HW_HINTS ablation: off = sem hardware no prompt → label distinto p/ não colidir com o baseline (hw on).
    if os.getenv("HW_HINTS", "on").strip().lower() in ("0", "false", "off", "no"):
        label += "+nohw"
    # PLAN_HINTS ablation: off = sem o PLANO de execução (só estrutura+schema) → label distinto.
    if os.getenv("PLAN_HINTS", "on").strip().lower() in ("0", "false", "off", "no"):
        label += "+noplan"
    # rewrite-focus (RW): on = escopa o FIND a só-reescrita (proíbe propor índice/DDL) → label distinto.
    if os.getenv("REWRITE_FOCUS", "off").strip().lower() in ("1", "true", "on", "yes"):
        label += "+rwfocus"
    # Lever D: regras CORRETIVAS de dialeto no writer (alvo = os `mechanics_failed` do engine). Dois modos
    # com labels DISTINTOS — medem coisas diferentes: reativo (disparado pelo erro do SGBD) × blanket (teto
    # enviesado, todas as regras sempre). Colidir os dois no mesmo bucket do store invalidaria a leitura.
    _dh = os.getenv("DIALECT_HINTS", "off").strip().lower()
    if _dh == "blanket":
        label += "+dialectall"
    elif _dh in ("1", "true", "on", "yes"):
        label += "+dialecthints"
    # Lever SEM: checklist de preservação semântica no writer (alvo = os `equivalence_failed`).
    if os.getenv("SEMANTIC_GUARD", "off").strip().lower() in ("1", "true", "on", "yes"):
        label += "+semguard"
    # SCHEMA_LINKING: colunas reais das tabelas no prompt do writer (coder aided + raw architect = B-write).
    # Lever de DESFECHO (muda o land) → precisa de label distinto pra não colidir no store com uma run sem ele.
    if os.getenv("SCHEMA_LINKING", "off").strip().lower() in ("1", "true", "on", "yes"):
        label += "+schemalink"
    # SCHEMA_LINKING_FIND (B-find): schema no prompt de DECISÃO do architect (o FIND, não o WRITE). Isolado
    # pra medir o efeito no FIND separado do WRITE.
    if os.getenv("SCHEMA_LINKING_FIND", "off").strip().lower() in ("1", "true", "on", "yes"):
        label += "+schemafind"
    # Lever #18 (minimal-reasoning-efficiency): re-estratégia recebe a CLASSE REAL do fracasso anterior.
    if os.getenv("TRUE_FAILURE_FEEDBACK", "off").strip().lower() in ("1", "true", "on", "yes"):
        label += "+truefb"
    # Pré-check formal `[4]`: lever de EFICIÊNCIA (barra cedo, não deve mudar o land-set) — ainda assim
    # marcado no label, senão dois runs com filtros diferentes colidiriam no mesmo bucket do store.
    if os.getenv("SEMANTIC_PRECHECK", "off").strip().lower() in ("1", "true", "on", "yes"):
        label += "+precheck"
    # CEILING: FIND no cloud (deepseek-reasoner) — mede o teto de capacidade do FIND. Label distinto: um run
    # com FIND-cloud NÃO pode colidir no store com o FIND-local (é outro modelo decidindo a estratégia).
    if os.getenv("FIND_BACKEND", "local").strip().lower() == "cloud":
        label += "+cloudfind"
    return label


def _deduplicate_suggestions(suggestions: list[str]) -> list[str]:
    if len(suggestions) <= 1:
        return suggestions
    try:
        from langchain_ollama import OllamaEmbeddings
        embedder = OllamaEmbeddings(
            model=get_embedding_model(),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
        embeddings = [np.array(e) for e in embedder.embed_documents(suggestions)]
        kept: list[str] = []
        kept_embeddings: list[np.ndarray] = []
        for text, emb in zip(suggestions, embeddings):
            norm = np.linalg.norm(emb)
            if norm == 0:
                continue
            is_duplicate = any(
                np.dot(emb, k) / (norm * np.linalg.norm(k)) >= SUGGESTION_SIMILARITY_THRESHOLD
                for k in kept_embeddings
            )
            if not is_duplicate:
                kept.append(text)
                kept_embeddings.append(emb)
        return kept
    except Exception:
        return suggestions

def _classify_outcome(state: dict, approved: bool) -> str:
    """How a rewrite attempt ended, for the Direction B->A discovery feed. Deterministic from
    the executor's status + reflection_errors. Lets the discoverer separate a technique the
    model LANDED (rewrite_correct) from one it reached for but botched — distinguishing a real
    model limit from a mechanical slip that a hint/repair could fix.
      rewrite_correct            — approved (hash + time gates passed)
      equivalence_failed_*       — SQL ran/parsed but changed the result set (structural drop or hash S!=1)
      mechanics_failed           — invalid SQL / DB execution error / duplicate (transcription, not idea)
      no_gain                    — valid & equivalent but not faster/cheaper (no opportunity)
    """
    # The architect hit its single-call deadline (runaway <think>, no usable SQL produced) → a capability
    # finding (model ran away), recorded as "timeout" (class-c). The PARTIAL reasoning_trace is still saved.
    if state.get("architect_timed_out"):
        return "timeout"
    if approved:
        # Approved but sub-threshold (no best result tracked) = valid & equivalent yet no real
        # gain above the noise floor → "no_gain" per the pre-registered taxonomy, not a true win.
        return "rewrite_correct" if state.get("best_approved_sql") else "no_gain"
    # state["status"] is UNRELIABLE here: on a failed rewrite the pipeline continues to
    # index_advisor / index_simulation_validator / persuasion_layer, which each overwrite "status"
    # before this runs (so a data_loss failure shows up as "failed"). Classify from STABLE signals —
    # the executor's reflection_errors (never touched downstream) and validation_result.
    blob = " ".join(state.get("reflection_errors", []) or []).lower()
    vr = state.get("validation_result") or {}
    # Equivalence could NOT be checked — the original timed out (even on the verification instance) so no
    # ground-truth result set exists to compare against. NOT a structural/semantic failure: the rewrite
    # ran, we just couldn't establish equivalence. Must precede the structural branch (post fix #6 the
    # advisory Data_Loss hints can co-occur on the SAME attempt — they must NOT mislabel it).
    if "equivalence unverified" in blob or vr.get("integrity_unverified"):
        return "unverified"
    # Result-set hash mismatch (the REAL S=1 signal): agnostic state flag, not message text.
    if vr.get("integrity_ok") is False:
        return "equivalence_failed_semantic"
    # Invalid SQL / DB execution error / duplicate (emitted by the executor node itself).
    if (
        "syntax error" in blob or "execution error" in blob or "unknown sources" in blob
        or "duplicate_attempt" in blob or "no output" in blob
    ):
        return "mechanics_failed"
    # Structural guard is ADVISORY (fix #6): Data_Loss/Cartesian/Structural are HINTS, not a verdict.
    # Demoted to AFTER the execution signals above — only classify as structural if NOTHING else
    # explained the failure (S=1 is the equivalence authority; a genuine drop now fails at EXECUTION).
    if any(m in blob for m in ("data_loss", "cartesian", "structural_error")):
        return "equivalence_failed_structural"
    return "no_gain"


def _attempts_to_label(state: dict, raw_sql: str, final_sql: str | None, approved: bool, limit: int = 5) -> list[tuple[str, bool]]:
    """Every DISTINCT rewrite attempt worth labeling for the Direction B->A discovery feed: the
    final attempt PLUS each distinct rejected attempt. A model can reach the right technique on an
    early attempt and then DEGRADE on a later retry (e.g. revert to the original) — labeling only
    the final attempt loses that discovery and would UNDER-COUNT the cross-model convergence
    criterion (b), which is what defends the thesis ("models converge, the researcher didn't pick
    it"). Returns [(sql, verified)]; verified=True only for an approved final rewrite (so the labeler
    names what it APPLIED), False otherwise (names what it was TRYING to apply). Attempts identical
    to the original are skipped (no technique). Capped at `limit` to bound post-hoc labeler calls."""
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()
    raw = (raw_sql or "").strip()
    final_attempt = final_sql if approved else state.get("optimized_sql")
    # attempt_history captures EVERY non-empty prior attempt (any failure type), unlike
    # rejected_sql_attempts which only holds structural rejects (loop detection). This is what makes
    # execution-error attempts that reached the right technique visible to the discovery feed.
    ordered = [(final_attempt, approved)] + [(a, False) for a in (state.get("attempt_history") or [])]
    for cand, verified in ordered:
        s = (cand or "").strip()
        if s and s != raw and s not in seen:
            out.append((cand, verified))
            seen.add(s)
        if len(out) >= limit:
            break
    return out


router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class SQLInput(BaseModel):
    sql: str
    hw_override: dict | None = None  # e.g. {"cache_hit_ratio_pct": 30, "work_mem_mb": 4}


class OptimizationResponse(BaseModel):
    original_sql: str
    optimized_sql: str | None
    approved: bool
    production_path: str | None = None  # how the SQL was produced: detector:<t> | free_form | free_form_corrected:<local|cloud> | none
    discovery_hint: str | None = None  # set when production matched no detector — invites exploring in discovery mode
    cache_hit: bool
    strategy_cache_hit: bool = False  # #12: o bypass reusou estratégia cacheada (pulou reasoning) — p/ a curva
    metrics: dict | None
    plans_file: str | None
    suggestions: list[str]
    discovered_techniques: list[str] = []  # suggestions + labeler-named technique(s) (the discovery feed)
    scale_divergence: bool = False
    float_sensitive_sf1_only: bool = False  # PROVISIONAL land: S=1 only on SF1 + float-threshold predicate (#17)
    index_recommendations: list[dict] | None = None
    persuasion_report: dict | None = None


def _save_plans(plans: dict) -> str:
    os.makedirs(PLANS_DIR, exist_ok=True)
    filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".json"
    path = os.path.join(PLANS_DIR, filename)
    with open(path, "w") as f:
        json.dump(plans, f, indent=2)
    existing = sorted(
        (os.path.join(PLANS_DIR, f) for f in os.listdir(PLANS_DIR) if f.endswith(".json")),
        key=os.path.getmtime,
    )
    for old in existing[:-MAX_PLANS_FILES]:
        os.remove(old)
    return path


def _build_result(sql: str, state: dict) -> dict:
    validation = state.get("validation_result") or {}
    timing = validation.get("execution_time", {})
    metrics = None
    execution_plans = None

    plans_file = None

    if validation:
        metrics = {
            "execution_time": {
                "original_ms": timing.get("original_ms"),
                "optimized_ms": timing.get("optimized_ms"),
                "delta_ms": timing.get("delta_ms"),
                "improvement_pct": timing.get("improvement_pct"),
                # When the original timed out we can't measure its true time — report the floor
                # (timeout budget) and the resulting lower-bound improvement instead of nothing.
                "original_floor_ms": timing.get("original_floor_ms"),
                "improvement_lower_bound_pct": timing.get("improvement_lower_bound_pct"),
                # SCALE LABEL — must travel with the numbers. When the full-scale gain is
                # unmeasurable but S=1 holds, the executor re-measures BOTH sides on the verify
                # instance and marks perf_scale="SF1". This fixed field list used to DROP that
                # mark, so reduced-scale timings reached the store looking full-scale — the
                # numbers were sound (same instance on both sides) but unlabelled, and any
                # reader mixing them with full-scale ones drew wrong conclusions. Found 23/08.
                "perf_scale": timing.get("perf_scale"),
            },
            "planner_cost": {
                "original": validation.get("original_cost"),
                "optimized": validation.get("optimized_cost"),
                "reduced": validation.get("cost_reduced"),
            },
            "integrity": {
                "hash_match": validation.get("integrity_ok"),
            },
            "inference_ms": state.get("inference_ms"),
            "index_advisor_inference_ms": state.get("index_advisor_inference_ms"),
            "reflection_iterations": state.get("reflection_iterations", 0),
        }

        plans = validation.get("plans", {})
        if plans:
            execution_plans = {
                "architect_saw": state.get("explain_plan", {}).get("plan"),
                "original_analyze": plans.get("original"),
                "optimized_analyze": plans.get("optimized"),
                "inference_ms": state.get("inference_ms"),
                "index_advisor_inference_ms": state.get("index_advisor_inference_ms"),
                # PROVENANCE (arch B): which learned rules were injected + the model's raw reasoning.
                "rules_in_prompt": state.get("rules_in_prompt", []),
                "reasoning_trace": state.get("reasoning_trace", ""),
            }
            plans_file = _save_plans(execution_plans)

    # no_change short-circuit produces no validation_result — inference timing must survive regardless
    if metrics is None:
        metrics = {
            "execution_time": {
                "original_ms": None,
                "optimized_ms": None,
                "delta_ms": None,
                "improvement_pct": None,
            },
            "inference_ms": state.get("inference_ms"),
            "index_advisor_inference_ms": state.get("index_advisor_inference_ms"),
            "reflection_iterations": state.get("reflection_iterations", 0),
        }

    # Use best approved result across all retry attempts; fall back to last approved if no best tracked
    best_sql = state.get("best_approved_sql")
    last_approved = state.get("approved", False)
    approved = best_sql is not None or last_approved
    final_sql = best_sql or (state.get("optimized_sql") if last_approved else None)
    # S=1 was confirmed on the verification instance because the FULL-scale original was mis-executed by
    # the engine at scale (full-scale S≠1, SF1 S=1) — surfaced for the record and the run result.
    scale_divergence = bool(
        (state.get("best_validation_result") or state.get("validation_result") or {}).get("scale_divergence")
    )
    # PROVISIONAL land tag: equivalence established ONLY on the SF1 instance AND the query compares against a
    # float aggregate threshold (`> avg/sum ...`) → the S=1 is scale-fragile (SF1 too small to exercise the
    # float-borderline divergence). Not a rejection — flagged for cross-engine / full-scale adjudication. #17
    float_sensitive_sf1_only = bool(
        (state.get("best_validation_result") or state.get("validation_result") or {}).get("float_sensitive_sf1_only")
    )

    # When returning best_approved_sql, use its corresponding validation metrics — not the last attempt's
    if best_sql and state.get("best_validation_result"):
        best_validation = state["best_validation_result"]
        best_timing = best_validation.get("execution_time", {})
        metrics = {
            "execution_time": {
                "original_ms": best_timing.get("original_ms"),
                "optimized_ms": best_timing.get("optimized_ms"),
                "delta_ms": best_timing.get("delta_ms"),
                "improvement_pct": best_timing.get("improvement_pct"),
                "original_floor_ms": best_timing.get("original_floor_ms"),
                "improvement_lower_bound_pct": best_timing.get("improvement_lower_bound_pct"),
                # SCALE LABEL — must travel with the numbers. When the full-scale gain is
                # unmeasurable but S=1 holds, the executor re-measures BOTH sides on the verify
                # instance and marks perf_scale="SF1". This fixed field list used to DROP that
                # mark, so reduced-scale timings reached the store looking full-scale — the
                # numbers were sound (same instance on both sides) but unlabelled, and any
                # reader mixing them with full-scale ones drew wrong conclusions. Found 23/08.
                "perf_scale": best_timing.get("perf_scale"),
            },
            "planner_cost": {
                "original": best_validation.get("original_cost"),
                "optimized": best_validation.get("optimized_cost"),
                "reduced": best_validation.get("cost_reduced"),
            },
            "integrity": {
                "hash_match": best_validation.get("integrity_ok"),
            },
            "inference_ms": state.get("inference_ms"),
            "index_advisor_inference_ms": state.get("index_advisor_inference_ms"),
            "reflection_iterations": state.get("reflection_iterations", 0),
        }
        plans = best_validation.get("plans", {})
        if plans:
            execution_plans = {
                "architect_saw": state.get("explain_plan", {}).get("plan"),
                "original_analyze": plans.get("original"),
                "optimized_analyze": plans.get("optimized"),
                "inference_ms": state.get("inference_ms"),
                "index_advisor_inference_ms": state.get("index_advisor_inference_ms"),
                # PROVENANCE (arch B): which learned rules were injected + the model's raw reasoning.
                "rules_in_prompt": state.get("rules_in_prompt", []),
                "reasoning_trace": state.get("reasoning_trace", ""),
            }
            plans_file = _save_plans(execution_plans)

    deduped_suggestions = _deduplicate_suggestions(state.get("suggestions", []))

    # Persist suggestions for Direction B → A discovery pipeline.
    # When the full-scale original timed out, the exact improvement % is unknown (no original_ms),
    # so `improvement_pct` is None — but a verified-equivalent rewrite still carries a measured
    # LOWER BOUND (≥X% vs the timeout budget). Feed that to the store instead of None: otherwise the
    # heuristic_discoverer reads it as 0% gain (`improvement_pct or 0.0`) and under-weights a large
    # win when ranking techniques by frequency × correlation_delta (e.g. q1 decorrelation, ≥98%).
    exec_metrics = metrics["execution_time"] if metrics else {}
    improvement_pct = exec_metrics.get("improvement_pct")
    if improvement_pct is None:
        improvement_pct = exec_metrics.get("improvement_lower_bound_pct")

    # Free-form attempts are invisible to discovery: the free-form path never populates
    # `suggestions`. Label the technique the rewrite APPLIED (approved) or ATTEMPTED (rejected)
    # — post-hoc, no effect on the produced SQL — so BOTH free-form successes AND the
    # "right idea, broken mechanics" failures feed the Direction B->A loop. The outcome tag
    # lets the discoverer tell those two apart (see _classify_outcome).
    outcome = None if state.get("cache_hit") else _classify_outcome(state, approved)
    used_fallback = state.get("used_fallback", False)
    discovery_suggestions = list(deduped_suggestions)
    labeler_inference_ms = 0.0  # post-hoc labeling is also LLM inference — count it in the run total
    if used_fallback:
        # Label EVERY distinct attempt (final + rejected), not just the last one — otherwise a
        # technique reached on an early attempt is lost when a later retry degrades (e.g. reverts
        # to the original), under-counting cross-model convergence. Labels are deduped within the
        # run, so a run still casts ONE vote per technique cluster.
        candidates = _attempts_to_label(state, sql, final_sql, approved)
        if candidates:
            try:
                from src.connections import get_llm
                # reasoning=False: the labeler NAMES the technique (classification) — it does not need
                # chain-of-thought. With the reasoning model's CoT on, labeling 5 q9 attempts took ~31 min
                # and the <think> block broke the JSON parse (empty labels → lost FIND). Disabling it makes
                # the labeler fast and its output directly parseable.
                llm = get_llm(reasoning=False)
                backend = get_backend()
                for cand_sql, verified in candidates:
                    _t = time.perf_counter()
                    labels = label_freeform_techniques(sql, cand_sql, llm, verified=verified)
                    labeler_inference_ms += (time.perf_counter() - _t) * 1000
                    # Deterministic fact-check: drop labels the SQL contradicts (e.g. a "decorrelate"
                    # label while the correlated subquery is still present). The labeler is an LLM —
                    # often the weak model under test — and hallucinates techniques it did not apply,
                    # inflating cross-model convergence in the store. Observability-only: can only
                    # remove an unsupported label, never add one (no bias toward the thesis).
                    labels = backend.verify_technique_labels(sql, cand_sql, labels)
                    for lbl in labels:
                        if lbl not in discovery_suggestions:
                            discovery_suggestions.append(lbl)
            except Exception:
                pass  # fail-open: discovery enrichment must never break the response

    # TOTAL LLM inference for the run = generation (architect, all attempts) + index advisor + labeler.
    # Surfaced in metrics (breakdown kept) and persisted to the discovery store, so the offline cost of
    # producing a rewrite — the cost the per-query cache amortizes — is fully attributed.
    labeler_inference_ms = round(labeler_inference_ms, 1)
    total_inference_ms = round(
        (state.get("inference_ms") or 0.0)
        + (state.get("index_advisor_inference_ms") or 0.0)
        + labeler_inference_ms,
        1,
    )
    if metrics is not None:
        metrics["labeler_inference_ms"] = labeler_inference_ms
        metrics["total_inference_ms"] = total_inference_ms
        # tempo do WRITER (novo 2026-08-07 — antes subcontado; células antigas não têm o campo).
        # NÃO entra no total_inference_ms legado (comparabilidade); o T11 soma explicitamente.
        metrics["writer_inference_ms"] = round(float(state.get("writer_inference_ms") or 0.0), 1)
        # CUSTO do writer (novo 2026-08-15). Antes disto NENHUM dado de custo existia: a pergunta
        # "o writer mais barato é de fato mais barato?" só tinha a tabela de preços do provedor como
        # resposta, nunca medição nossa. tokens/chamada é o custo por reescrita; writer_calls conta as
        # tentativas (um retry do lint/pré-check custa de novo). Zero em writer local — fielmente.
        metrics["writer_tokens_in"] = int(state.get("writer_tokens_in") or 0)
        metrics["writer_tokens_out"] = int(state.get("writer_tokens_out") or 0)
        metrics["writer_calls"] = int(state.get("writer_calls") or 0)

    # PROVENANCE — how the rewrite was produced. Production is detector-only (NO LLM); free-form is
    # discovery (LLM writes), optionally repaired by the coder (local/cloud, flagged). Lets every record
    # state WHICH method reached the answer: Pillar 2 (detector) vs Pillar 1 (free-form) vs assisted (b→a).
    _prov = state.get("decomposed_provenance") or {}
    # b→a rescue = the corrector PRODUCED the approved SQL (from_corrector), not merely RAN. A failed
    # repair followed by a model retry that landed must NOT be mis-attributed to the coder (observed on q9).
    corrector_rescued = approved and state.get("from_corrector", False)
    if _prov.get("transform"):
        production_path = f"detector:{_prov['transform']}"
    elif used_fallback and corrector_rescued:
        # PROVENANCE FIX (2026-07-07): the :local/:cloud tag must reflect the ACTUAL writer (WRITE-agent),
        # not the stale FORMALIZER_API_BASE probe toggle. The corrector records `writer_used` in state
        # (writer_label(WRITER_BACKEND) → "cloud:deepseek-chat" | "local:qwen-coder"); derive the tag from
        # it so the store attribution (FIND-qwen / WRITE-cloud) is correct. Fallback to WRITER_BACKEND.
        _w = (state.get("writer_used") or os.getenv("WRITER_BACKEND", "local")).split(":")[0].strip().lower()
        production_path = "free_form_corrected:" + (_w or "local")
    elif used_fallback:
        production_path = "free_form"
    elif _prov.get("trigger") == "no_change":
        production_path = "no_change"
    else:
        production_path = _prov.get("trigger") or "none"

    # DISCOVERY CANDIDATE (#1/#2): a PRODUCTION run where NO detector matched (`none`) is a query
    # SHAPE with no formalized heuristic yet — flag it so the offline campaign / the DBA knows what to
    # explore, and hand the DBA to the "explore" (free-form) mode where the LLM may find one (and the
    # LLM index advisor runs). Empty when a detector fired, when free-form ran, or on cache hit.
    no_formalized_optimization = production_path == "none" and not state.get("cache_hit")
    discovery_hint = (
        "No formalized rewrite (detector) matched this query shape, and the index is a "
        "discovery-mode feature. Run with DISCOVERY_MODE=on to EXPLORE (free-form) — the LLM may "
        "find a rewrite and recommend an index (experimental, non-deterministic); whatever it reaches "
        "becomes a candidate to formalize as a detector."
    ) if no_formalized_optimization else None

    # The model's best REACHED attempt — EVIDENCE of the reach for the teachable gate (step 1). On a
    # landing it's the valid rewrite; on a failure it's the first prior attempt that at least PARSES and
    # restructures the original (syntax-garbage → None, so a weak model's HALLUCINATED label is not backed
    # by SQL and won't count as "another model reached it"). Built from the same attempts the labeler saw.
    reached_sql = final_sql if approved else None
    if reached_sql is None:
        _raw_norm = (sql or "").strip()
        for _cand, _v in _attempts_to_label(state, sql, final_sql, approved):
            _c = (_cand or "").strip()
            if not _c or _c == _raw_norm:
                continue
            try:
                _ok, _ = get_backend().validate_syntax(_c)
            except Exception:
                _ok = False
            if _ok:
                reached_sql = _c
                break

    save_run(
        suggestions=discovery_suggestions,
        improvement_pct=improvement_pct,
        raw_sql=sql,
        model=_model_label(),
        outcome=outcome,
        used_fallback=used_fallback,
        inference_ms=total_inference_ms,
        rules_in_prompt=state.get("rules_in_prompt", []),  # provenance: which rules guided this run
        db_type=get_db_type(),  # engine tag (PG/MySQL share a query_hash)
        # PATH: approved AND the corrector ran ⇒ a b→a mechanical rescue (model reached the technique,
        # coder cleaned the WRITE). Keeps the model's FIND capability separable from corrector assist.
        corrector_rescued=corrector_rescued,
        scale_divergence=scale_divergence,
        float_sensitive_sf1_only=float_sensitive_sf1_only,
        production_path=production_path,
        # Save the VALID rewrite only when one landed (approved). It is the LLM-authored worked
        # example a human studies when formalizing a discovered pattern into a deterministic detector
        # — never reused as raw SQL (see §3.4/§0.2).
        optimized_sql=final_sql if approved else None,
        reached_sql=reached_sql,
        reasoning_trace=state.get("reasoning_trace", ""),   # the model's CoT (<think>) — proof the FIND is reasoned
        metrics=metrics,                                     # full metric block (same as the manual run)
        errors=state.get("reflection_errors") or [],         # WHY it erred (rejection reasons); [] when it landed
        # 2-AGENTES: a ESTRATÉGIA do architect (o FIND) — persistida MESMO no b→b, p/ replay offline com o cloud-writer;
        # e QUEM escreveu (local/cloud) — atribuição do b→a (mede "FIND bom que só o cloud escreveu").
        strategy=state.get("strategy"),
        writer=state.get("writer_used"),
        strategy_attempts=state.get("strategy_attempts"),  # profundidade do Loop 2 (re-estratégia); 1=um passe
        adherence=state.get("adherence"),                   # o writer seguiu a técnica do FIND? (juiz de aderência)
    )

    # #12 persist/reinforce hook — mantém o validated-strategy cache atual (CONGELADO quando a persistência
    # está off, ex. held-out). Um run de HIT reforça/rebaixa a entrada pelo desfecho da re-validação; um land
    # FRESCO auto-persiste a estratégia chaveada pela FORMA (técnica derivada da assinatura). O piso de
    # precisão em reinforce() AUTO-CURA entradas que param de landar em queries novas. É o passo que torna o
    # cache "automático" (o land à mão vira dispensável). Inerte quando STRATEGY_CACHE(_PERSIST)=off.
    from src.pipeline.discovery import strategy_cache
    if strategy_cache.persist_enabled():
        _cid = state.get("cached_strategy_id")
        if _cid:
            strategy_cache.reinforce(_cid, landed=approved)          # re-validou um hit → reforça/rebaixa
        elif approved and state.get("strategy") and improvement_pct:
            strategy_cache.save_landed(sql, state["strategy"], gain=improvement_pct)  # land fresco → auto-persiste

    simulation_reports = state.get("simulation_reports") or None

    # INDEX-axis discovery store (explore mode only — the index advisor runs only in DISCOVERY mode now).
    # The index analog of save_run: what the LLM recommended + the simulated outcome + the model, so a
    # future deterministic candidate generator ('Pillar 2 for indexes') has a corpus to mine, and so we
    # can see what a weak model (llama) recommends vs a strong one. No-op when there are no reports.
    if simulation_reports:
        save_index_run(
            simulation_reports, sql,
            model=_model_label(),
            db_type=get_db_type(),
            rewrite_hints=state.get("rewrite_hints", []),
            reasoning_trace=state.get("index_reasoning_trace", ""),   # CoT of the index advisor
            inference_ms=state.get("index_advisor_inference_ms"),     # index-advisor latency
            workload_aware=state.get("index_workload_aware", False),  # generation FORM (per-query vs workload-aware)
        )

    return {
        "original_sql": sql,
        "optimized_sql": final_sql if approved else None,
        "approved": approved,
        "production_path": production_path,  # PROVENANCE: detector:<t> | free_form | free_form_corrected:<local|cloud> | none
        "discovery_hint": discovery_hint,  # set when production found NO formalized detector → invite the DBA to explore
        "scale_divergence": scale_divergence,  # RETIRED accept path — always False now (schema back-compat)
        "float_sensitive_sf1_only": float_sensitive_sf1_only,  # PROVISIONAL: S=1 only on SF1 + float threshold (#17)
        "cache_hit": state.get("cache_hit", False),
        "strategy_cache_hit": state.get("used_strategy_cache", False),  # #12: o bypass reusou estratégia cacheada (p/ a curva)
        "metrics": metrics,
        "plans_file": plans_file,
        "suggestions": deduped_suggestions,  # the architect's FREE-FORM suggestions (may be empty)
        "discovered_techniques": discovery_suggestions,  # suggestions + technique(s) the LABELER named for the
        # discovery feed (what went into the store) — surfaced here so a free-form win shows its technique
        # in the curl, not only in .cache/suggestions/ (e.g. q9: "restructure … into a single subquery with filters").
        "rules_in_prompt": state.get("rules_in_prompt", []),  # PROVENANCE: rule attribution, visible in the stream
        "index_recommendations": simulation_reports,
        "persuasion_report": state.get("persuasion_report") or None,
    }


@router.post("/input", response_model=OptimizationResponse)
def input_router(payload: SQLInput):
    """Receives raw SQL, runs it through the optimization pipeline and returns the rewritten query."""
    failed_suggestions = SemanticCache().lookup_failed(payload.sql)
    if failed_suggestions:
        return {
            "original_sql": payload.sql,
            "optimized_sql": None,
            "approved": False,
            "cache_hit": True,
            "metrics": None,
            "plans_file": None,
            "suggestions": failed_suggestions,
            "index_recommendations": None,
        }

    initial = {"raw_sql": payload.sql, "status": "", "reflection_iterations": 0}
    if payload.hw_override:
        initial["hw_override"] = payload.hw_override
    state = pipeline.invoke(initial)
    result = _build_result(payload.sql, state)

    if not result["approved"] and not state.get("cache_hit") and result["suggestions"]:
        SemanticCache().store_failed(payload.sql, result["suggestions"])

    return result


@router.post("/stream")
def stream_pipeline(payload: SQLInput):
    """Same as /input but streams progress events (SSE) as each node completes."""

    def generate():
        failed_suggestions = SemanticCache().lookup_failed(payload.sql)
        if failed_suggestions:
            result = {
                "original_sql": payload.sql,
                "optimized_sql": None,
                "approved": False,
                "cache_hit": True,
                "metrics": None,
                "plans_file": None,
                "suggestions": failed_suggestions,
                "index_recommendations": None,
            }
            yield f"data: {json.dumps({'done': True, 'result': result})}\n\n"
            return

        accumulated = {"raw_sql": payload.sql, "status": "", "reflection_iterations": 0}
        if payload.hw_override:
            accumulated["hw_override"] = payload.hw_override
        pipeline_start = time.perf_counter()
        node_start = pipeline_start

        try:
            for chunk in pipeline.stream(accumulated.copy(), stream_mode="updates"):
                now = time.perf_counter()
                for node_name, updates in chunk.items():
                    node_elapsed = round(now - node_start, 3)
                    total_elapsed = round(now - pipeline_start, 3)
                    accumulated.update(updates)

                    event = {
                        "node": node_name,
                        "status": updates.get("status", ""),
                        "node_elapsed_s": node_elapsed,
                        "total_elapsed_s": total_elapsed,
                    }
                    if node_name == "architect" and updates.get("optimized_sql"):
                        event["generated_sql"] = updates["optimized_sql"]
                    if node_name == "executor" and updates.get("reflection_errors"):
                        event["errors"] = updates["reflection_errors"]
                    yield f"data: {json.dumps(event)}\n\n"
                    node_start = now

            result = _build_result(payload.sql, accumulated)

            if not result["approved"] and not accumulated.get("cache_hit") and result["suggestions"]:
                SemanticCache().store_failed(payload.sql, result["suggestions"])

            yield f"data: {json.dumps({'done': True, 'result': result})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
