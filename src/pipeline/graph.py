from langgraph.graph import StateGraph, START, END
from src.pipeline.state import PipelineState
from src.pipeline.nodes.input_router import input_router
from src.pipeline.nodes.schema_analyst import schema_analyst
from src.pipeline.nodes.architect import architect, MAX_REFLECTION_ITERATIONS, _discovery_mode
from src.pipeline.nodes.executor import executor
from src.pipeline.backends import get_backend
from src.pipeline.nodes.index_advisor import index_advisor
from src.pipeline.nodes.index_simulation_validator import index_simulation_validator
from src.pipeline.nodes.corrector import corrector, corrector_enabled, two_agent_mode

import os

MAX_RETRY_ATTEMPTS = 2  # quality-based retries when improvement is weak
# CORRECTOR RE-WIRED 2026-06-28 (arm "2 agentes" / B): opt-in via CORRECTOR_ENABLED. When ON, a MECHANICAL
# botch (alias/scope/clause-order) routes to the coder (WRITE-agent) which REPAIRS preserving the strategy,
# then back to executor. OFF (default) = unaided (mechanical_write_hint reflection only) — the baseline is
# untouched. Coder is LOCAL (qwen2.5-coder:7b) or cloud (deepseek, oracle/ceiling) via the .env toggle.
MAX_CORRECTOR_ATTEMPTS = int(os.getenv("MAX_CORRECTOR_ATTEMPTS", "2"))   # tentativas do WRITER por estratégia
MAX_STRATEGY_ATTEMPTS = int(os.getenv("MAX_STRATEGY_ATTEMPTS", "3"))     # 2-agentes: estratégias DIFERENTES (o FIND itera = Loop 2)


def _route_after_architect(state: PipelineState) -> str:
    """2-agentes: architect DECIDE a estratégia → writer ESCREVE. Raw: architect ESCREVE → executor valida."""
    return "corrector" if two_agent_mode() else "executor"

# Rewrite→Index combination: when an APPROVED rewrite gives only a MARGINAL time gain, an index on the
# REWRITTEN plan may add more — escalate to the advisor to recommend rewrite + index together. Gated on
# BOTH a % threshold (gain is small) AND an absolute-time floor (the query still has real time to save —
# a 25% gain on a 200ms query is not worth an index; on a 60s query it is). cost≠time (~10×) means the
# index benefit the advisor predicts is a flag-for-review, not a guarantee.
REWRITE_INDEX_COMBO_THRESHOLD_PCT = float(os.getenv("REWRITE_INDEX_COMBO_THRESHOLD_PCT", "25"))
REWRITE_INDEX_COMBO_MIN_MS = float(os.getenv("REWRITE_INDEX_COMBO_MIN_MS", "1000"))


def _rewrite_is_marginal(state: PipelineState) -> bool:
    """True when an approved rewrite's measured time gain is below threshold AND the query still has
    meaningful time left to save — the trigger to try a rewrite+index combination. Returns False when
    the gain is unmeasured (e.g. original timed out → lower-bound, already a huge win)."""
    val = state.get("best_validation_result") or state.get("validation_result") or {}
    t = val.get("execution_time", {}) if isinstance(val, dict) else {}
    imp = t.get("improvement_pct")
    opt_ms = t.get("optimized_ms")
    if imp is None or opt_ms is None:
        return False  # timeout-floor (lower-bound win) or unmeasured — not a marginal case
    return imp < REWRITE_INDEX_COMBO_THRESHOLD_PCT and opt_ms > REWRITE_INDEX_COMBO_MIN_MS


def _suggestions_mention_index(state: PipelineState) -> bool:
    """True when at least one LLM suggestion mentions an index — used to trigger index_advisor."""
    keywords = ("index", "índice")
    return any(
        any(kw in s.lower() for kw in keywords)
        for s in state.get("suggestions", [])
    )


def _suggestions_only_index(state: PipelineState) -> bool:
    """True when ALL LLM suggestions are about indexes — no SQL rewrite ideas remain.
    Used to skip quality retry: if the LLM's only feedback is DDL, another rewrite attempt won't help."""
    suggestions = state.get("suggestions", [])
    if not suggestions:
        return False
    keywords = ("index", "índice")
    return all(
        any(kw in s.lower() for kw in keywords)
        for s in suggestions
    )


def _route_after_executor(state: PipelineState) -> str:
    errors = state.get("reflection_errors", [])
    iterations = state.get("reflection_iterations", 0)
    cache_hit = state.get("cache_hit", False)

    # CORRECTOR DEPRECATED (2026-06-18): a separate code model (the cloud 670B coder) did not earn its
    # keep — on hard WRITE botches it reverted/duplicated instead of repairing, and it broke the "small
    # LOCAL model" claim. Its job — helping the model produce VALID SQL for the technique IT chose — is now
    # done by GENERIC, mechanical REFLECTION hints (executor → backend.mechanical_write_hint: clause order,
    # double WHERE, per-group avg). The reasoning model self-corrects from an actionable diagnostic; the
    # FIND stays unaided (the hint names no technique, no benchmark identifier — see metodologia §0). What
    # reflection can't fix, the deterministic DETECTOR handles in production. No second model, fully local.

    # ===== Modo 2-AGENTES (TWO_AGENT_MODE): architect DECIDE → writer ESCREVE; cascata de feedback =====
    if two_agent_mode():
        wa = state.get("corrector_attempts", 0)   # tentativas do WRITER pra estratégia atual
        sa = state.get("strategy_attempts", 0)     # estratégias DIFERENTES do architect (o FIND itera)
        if errors:
            if wa < MAX_CORRECTOR_ATTEMPTS:
                return "corrector"   # erro de WRITE → writer RE-ESCREVE a MESMA estratégia
            if sa < MAX_STRATEGY_ATTEMPTS:
                return "architect"   # writer ESGOTOU (estratégia inescrevível) → RE-ESTRATÉGIA
            # ambos esgotaram → b→b → cai no índice/END abaixo
        elif (
            state.get("retry_hint") and sa < MAX_STRATEGY_ATTEMPTS
            and not cache_hit and not state.get("cached_strategy_id")
            and not _suggestions_only_index(state)
        ):
            return "architect"       # no-gain (válido mas inútil) → RE-ESTRATÉGIA (Loop 2)
            # #12/#10b (2026-07-13): NÃO re-estratégia quando a estratégia veio do CACHE (cached_strategy_id).
            # O cache é a melhor estratégia validada pra esta forma; se deu no_gain, re-raciocinar não ajuda e
            # gira sem convergir (o q38 rodou até 900s re-propondo a mesma técnica). Hit no_gain → SAI como no_gain.
        # land OU esgotado → índice/END abaixo (pula o roteamento raw)
    else:
        # ===== Modo RAW (+ B1 corrector-fallback) — roteamento original =====
        # B1: botch MECÂNICO + CORRECTOR_ENABLED → coder repara preservando; bounded por corrector_attempts.
        if (
            errors and corrector_enabled() and get_backend().is_mechanical_failure(errors)
            and state.get("corrector_attempts", 0) < MAX_CORRECTOR_ATTEMPTS
        ):
            return "corrector"
        # Reflexão por erro: volta pro architect com os erros (mesmo modelo se auto-corrige via hints).
        if errors and iterations < MAX_REFLECTION_ITERATIONS:
            return "architect"
        # Quality retry: aprovado mas sem ganho → tenta estratégia diferente (pula se só sobrou índice).
        if (
            state.get("retry_hint") and state.get("retry_attempts", 0) < MAX_RETRY_ATTEMPTS
            and not cache_hit and not _suggestions_only_index(state)
        ):
            return "architect"

    # Rewrite loop exhausted — decide whether to escalate to index advisor:

    # The INDEX axis is LLM-PROPOSED (non-deterministic candidate selection) → it runs ONLY in
    # EXPLORE/DISCOVERY mode. The serious advisor (production, DISCOVERY_MODE=off) does DETERMINISTIC
    # REWRITE only (detectors). (Future: a rule-based deterministic index candidate generator would
    # bring the index axis into the serious advisor too — the "Pillar 2 for indexes".)
    if not _discovery_mode():
        return END

    # 1. Rewrite failed entirely (approved=False)
    if not state.get("approved", False):
        return "index_advisor"

    # 2. All attempts were no_change — SQL cannot be improved, DDL may help
    if state.get("best_approved_sql") is None and not cache_hit:
        return "index_advisor"

    # 3. Rewrite succeeded but LLM explicitly cited an index opportunity in suggestions
    if _suggestions_mention_index(state) and not cache_hit:
        return "index_advisor"

    # 4. Rewrite succeeded but the time gain is MARGINAL — an index on the REWRITTEN plan may add more.
    # The advisor (and simulation) target best_approved_sql, so the index is recommended for the world
    # where the rewrite is applied — not the original plan, which no longer exists.
    if state.get("approved") and not cache_hit and _rewrite_is_marginal(state):
        return "index_advisor"

    return END


def build_graph() -> StateGraph:
    graph = StateGraph(PipelineState)

    graph.add_node("input_router", input_router)
    graph.add_node("schema_analyst", schema_analyst)
    graph.add_node("architect", architect)
    graph.add_node("executor", executor)
    graph.add_node("index_advisor", index_advisor)
    graph.add_node("index_simulation_validator", index_simulation_validator)
    graph.add_node("corrector", corrector)  # WRITE-agent (opt-in CORRECTOR_ENABLED) — arm "2 agentes"
    # persuasion_layer (DBA-facing "Create?" classification) is OUT of the path — it is product framing,
    # not needed for MEASUREMENT. The index recording the matrix consumes (save_index_run →
    # .cache/index_suggestions) reads simulation_reports DIRECTLY in input_router, independent of it.
    # Kept on disk as DORMANT/future work (like the deterministic detector), not registered as a node.

    graph.add_edge(START, "input_router")
    graph.add_edge("input_router", "schema_analyst")
    graph.add_edge("schema_analyst", "architect")
    # architect → executor (raw) | writer/corrector (2-agentes): o architect DECIDE, o writer ESCREVE
    graph.add_conditional_edges(
        "architect",
        _route_after_architect,
        {"executor": "executor", "corrector": "corrector"},
    )
    graph.add_conditional_edges(
        "executor",
        _route_after_executor,
        {
            "architect": "architect",
            "corrector": "corrector",
            "index_advisor": "index_advisor",
            END: END,
        },
    )
    graph.add_edge("corrector", "executor")  # repaired SQL → re-validate (S=1 + tempo)
    graph.add_edge("index_advisor", "index_simulation_validator")
    graph.add_edge("index_simulation_validator", END)

    return graph.compile()


pipeline = build_graph()
