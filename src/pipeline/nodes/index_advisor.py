import json
import logging
import os
import re
import time

from langchain_core.messages import HumanMessage, SystemMessage

from src.connections import get_llm
from src.pipeline.backends import get_backend
from src.pipeline.nodes.architect import _reasoning_flag
from src.pipeline.state import PipelineState

log = logging.getLogger(__name__)

# Number of co-located workload queries shown to the model in WORKLOAD-AWARE generation (token budget).
_WORKLOAD_DIGEST_CAP = int(os.getenv("INDEX_WORKLOAD_DIGEST_CAP", "8"))


def _index_workload_aware() -> bool:
    """Second generation FORM (professor asked to test both): when on, the WORKLOAD goes INTO the index
    prompt so the model can favor columns serving MANY queries. Off = per-query (target-only) generation —
    the workload is still measured AFTER, in simulation. `INDEX_WORKLOAD_AWARE=on|off` (default off)."""
    return os.getenv("INDEX_WORKLOAD_AWARE", "").strip().lower() in ("1", "true", "on", "yes")


def _build_workload_digest(query_tables, target_sql: str) -> str | None:
    """Compact digest of the OTHER workload queries that share the target's tables — the model reads their
    SQL to infer which columns are shared across the workload. Capped for token budget (logs what's cut)."""
    from src.pipeline.nodes.index_simulation_validator import _workload_for_table
    seen: dict[str, str] = {}
    for t in query_tables:
        for w in _workload_for_table(t, target_sql):
            seen.setdefault(w["source"], w["sql"])
    if not seen:
        return None
    items = list(seen.items())
    shown = items[:_WORKLOAD_DIGEST_CAP]
    lines = ["=== WORKLOAD (other queries that also use these tables — prefer index columns that speed up "
             "MANY of them, not just this one) ==="]
    for src, sql in shown:
        lines.append(f"-- {src}\n{sql}")
    if len(items) > _WORKLOAD_DIGEST_CAP:
        log.info(f"[index_advisor] workload digest capped at {_WORKLOAD_DIGEST_CAP}/{len(items)} co-located queries")
        lines.append(f"(+{len(items) - _WORKLOAD_DIGEST_CAP} more queries on these tables, omitted)")
    return "\n".join(lines)


def _extract_index_specs(text: str) -> dict | None:
    # With REASONING=on the model can emit <think>…</think> inline, whose braces corrupt the
    # greedy JSON match below (the SAME failure the labeler had — see project_labeler_no_cot).
    # Strip closed think blocks, then drop any unclosed leading think before matching the JSON.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if "indexes" in result:
                return result
        except Exception:
            pass
    return None


def index_advisor(state: PipelineState) -> PipelineState:
    # FAIL-OPEN wrapper: a crash in the index node must NEVER abort the whole pipeline (it would lose the
    # rewrite record AND return an empty result). On any error: log the full traceback (so we SEE the cause)
    # and return gracefully with no index — the run still reaches END and persists the rewrite.
    try:
        return _index_advisor_impl(state)
    except Exception as e:
        log.exception(f"[index_advisor] FAILED — fail-open, no index this run: {e}")
        return {**state, "index_specs": [], "status": "index_advisor_error",
                "index_workload_aware": _index_workload_aware()}


def _index_advisor_impl(state: PipelineState) -> PipelineState:
    # Index the query that will ACTUALLY run: when a rewrite was approved (marginal-gain combo, or the
    # LLM cited an index), recommend the index for the REWRITTEN plan — the original plan no longer
    # exists. Falls back to the original when no rewrite landed (rewrite failed / all no_change).
    target_sql = state.get("best_approved_sql") or state["raw_sql"]
    schema_context = state.get("schema_context", {})
    system_prompt = state.get("system_prompt", "You are a database optimization expert.")

    backend = get_backend()

    # Run ANALYZE on query tables so the planner uses accurate statistics
    query_tables = backend.extract_references(target_sql)
    backend.run_analyze(list(query_tables))

    # Fresh EXPLAIN after ANALYZE — of the rewritten query when combining
    explain = backend.explain(target_sql)
    # PLAN_HINTS ablation (Gate 1): withhold the plan from the index advisor too — else `PLAN_HINTS=off`
    # only cegava o architect (rewrite), and the ablation couldn't measure the INDEX axis. The index axis is
    # INHERENTLY plan-driven (full scans / cardinality live only in the plan, not in the SQL text), so this is
    # where "plan-driven" should show most. NOTE: the ANALYZE above still runs — the DB stats state is a
    # SEPARATE, fixed part of the setup; only the plan TEXT is withheld from the prompt (builder renders
    # "Error: withheld…" → the model recommends from query STRUCTURE + schema only).
    if os.getenv("PLAN_HINTS", "on").strip().lower() in ("0", "false", "off", "no"):
        explain = {"error": "withheld for ablation (PLAN_HINTS=off) — recommend from the query STRUCTURE + schema only"}

    # Backend builds the prompt with its own index types and syntax rules.
    # Pass suggestions from the architect — they contain free-form analysis of the full
    # query including correlated subqueries, which the index_advisor can use as additional signal.
    # Also pass the executor's causal hints from failed rewrite attempts: they often name the
    # structural limit and the index type that resolves it (e.g. "non-sargable LIKE — a
    # trigram/GIN index is required"), turning a stochastic discovery into a deterministic one.
    suggestions = state.get("suggestions") or []
    rewrite_hints = state.get("rewrite_hints") or []
    advisor_signals = suggestions + [h for h in rewrite_hints if h not in suggestions]
    # FORM 2 (workload-aware): inject the co-located workload into the prompt; FORM 1 (per-query): None.
    workload_aware = _index_workload_aware()
    workload_digest = _build_workload_digest(query_tables, target_sql) if workload_aware else None
    prompt = backend.build_index_advisor_prompt(
        target_sql, explain, schema_context, query_tables,
        suggestions=advisor_signals, workload_digest=workload_digest,
    )

    llm = get_llm(reasoning=_reasoning_flag())   # EXPLICIT thinking control — same reasoning axis as the architect
    node_start = time.perf_counter()
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=prompt)]
    # STREAM (not invoke): Ollama surfaces the CoT in additional_kwargs['reasoning_content'] PER-CHUNK
    # while streaming — a plain invoke() does NOT populate it, so the trace was lost. This mirrors the
    # architect's proven streaming capture. content + thinking are accumulated from the chunks.
    # DEFENSIVE: a streaming hiccup must NOT abort the whole pipeline — fall back to invoke (CoT may be lost).
    try:
        parts, think_parts = [], []
        for chunk in llm.stream(messages):
            parts.append(getattr(chunk, "content", "") or "")
            tk = (getattr(chunk, "additional_kwargs", {}) or {}).get("reasoning_content") or ""
            if tk:
                think_parts.append(tk)
        content = "".join(parts)
        thinking = "".join(think_parts)
    except Exception as e:
        log.warning(f"[index_advisor] streaming failed ({e}); falling back to invoke (CoT may be lost)")
        r = llm.invoke(messages)
        content = r.content or ""
        thinking = (r.additional_kwargs or {}).get("reasoning_content") or ""
    elapsed_ms = round((time.perf_counter() - node_start) * 1000, 1)

    reasoning_trace = (f"<think>\n{thinking}\n</think>\n\n" if thinking else "") + content

    log.info(f"[index_advisor] LLM response: {content[:500]}")

    parsed = _extract_index_specs(content)

    if parsed is None or parsed.get("no_index_possible"):
        log.info("[index_advisor] no actionable index recommendation")
        return {**state, "index_specs": [], "status": "no_index_found",
                "index_advisor_inference_ms": elapsed_ms,
                "index_workload_aware": workload_aware,  # generation FORM (per-query vs workload-aware)
                "index_reasoning_trace": reasoning_trace}  # CoT — why it chose (or chose no) index

    index_specs = []
    for spec in parsed.get("indexes", []):
        if not spec.get("target") or not spec.get("property"):
            continue
        ddl = backend.generate_index_ddl(spec)
        if ddl is None:
            log.info(
                f"[index_advisor] skipping {spec['target']}.{spec['property']} "
                f"— {spec.get('index_type', '?')} index not applicable for this column type"
            )
            continue
        spec["ddl"] = ddl
        index_specs.append(spec)
        log.info(f"[index_advisor] {spec['ddl']} — {spec.get('estimated_benefit', '')}")

    return {
        **state,
        "index_specs": index_specs,
        "status": "index_advised",
        "index_advisor_inference_ms": elapsed_ms,
        "index_workload_aware": workload_aware,  # generation FORM (per-query vs workload-aware)
        "index_reasoning_trace": reasoning_trace,  # CoT — why it chose these columns/index types
    }
