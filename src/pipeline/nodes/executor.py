import json
import os
import re

from src.pipeline.backends import get_backend
from src.pipeline.cache.semantic_cache import SemanticCache
from src.pipeline.state import PipelineState


def _app_mode() -> bool:
    """APPLICATION mode (a formalized heuristic is being applied) vs neutral DISCOVERY mode."""
    return os.getenv("APPLY_HEURISTICS", "").strip().lower() in ("1", "true", "on", "yes")


def _rich_equivalence_feedback() -> bool:
    """OPT-IN richer S≠1 feedback (`RICH_EQUIVALENCE_FEEDBACK=on`, default off). When off the executor path
    is byte-identical to before. When on, an S≠1 rejection on the SAMPLE HASH (same/diff content) is
    enriched with a row-level DIFF — the engine's measured truth (counts + diverging rows), NEVER the
    technique. A WRITE-help arm to test if 'this rewrite isn't equivalent' becomes actionable enough to land."""
    return os.getenv("RICH_EQUIVALENCE_FEEDBACK", "").strip().lower() in ("1", "true", "on", "yes")


def _equivalence_diff(backend, raw_sql: str, optimized_sql: str, opt_timeout: int | None) -> str:
    """Best-effort, anti-bias-safe S≠1 diagnostic: sample both result sets and report the count + the first
    DIVERGING rows. The engine's truth (no technique named). NEVER raises — returns '' on any failure."""
    try:
        sr = getattr(backend, "sample_rows", None)
        if sr is None:
            return ""
        orig_rows, oerr = sr(raw_sql)
        opt_rows, perr = sr(optimized_sql, timeout_s=opt_timeout)
        if orig_rows is None or opt_rows is None:
            return ""
        oset, pset = set(orig_rows), set(opt_rows)
        only_yours = [r for r in opt_rows if r not in oset][:3]
        only_orig = [r for r in orig_rows if r not in pset][:3]
        parts = [f"Sample diff (sorted, {len(orig_rows)} original rows vs {len(opt_rows)} of yours):"]
        if only_yours:
            parts.append("rows YOURS returns that the original does NOT: " + " | ".join(only_yours))
        if only_orig:
            parts.append("rows the ORIGINAL returns that yours does NOT: " + " | ".join(only_orig))
        if not only_yours and not only_orig:
            parts.append("same rows in the sample — divergence is in ORDER or in duplicate counts; check ORDER BY and whether a JOIN/UNION changed multiplicity.")
        return "\n".join(parts)
    except Exception:
        return ""

# Keys carrying runtime measurements, planner estimates or labels — not plan structure.
# A plan compared without these is the physical execution tree: node types, relations,
# join/scan strategies and conditions. Same tree = same execution, regardless of how the
# estimates were bookkept (e.g. predicate moved into an inlined CTE shifts Total Cost
# by a fraction while the physical plan is unchanged).
_PLAN_NOISE_TOKENS = (
    "Actual", "Time", "JIT", "Workers", "Alias", "Blocks", "Sort Space",
    "Sort Method", "Memory", "Batches", "Rows Removed", "Heap Fetches", "Disk",
    "Cost", "Plan Rows", "Plan Width",
    # MySQL FORMAT=JSON estimate keys
    "cost_info", "rows_examined", "rows_produced", "filtered",
)


def _backend_hint(backend, name: str, fallback: str) -> str:
    """Fetch an engine-specific reflection hint from the backend, or use a generic fallback.

    Keeps the executor node agnostic: SQL-flavored advice (join-order anchor, MATERIALIZED CTE,
    IN/EXISTS + DISTINCT) lives on the relational backend; other engines supply their own hint or
    fall back to generic text that says *what* failed without assuming a query language.
    """
    fn = getattr(backend, name, None)
    return fn() if fn is not None else fallback


def _plan_signature(plan) -> str | None:
    """Canonical signature of an execution plan: structure + predicates, stripped of
    runtime measurements and normalized so alias renames (e.g. after CTE inlining)
    don't mask identical plans."""
    if not plan:
        return None

    alias_map: dict[str, str] = {}

    def collect_aliases(node):
        if isinstance(node, dict):
            rel, alias = node.get("Relation Name"), node.get("Alias")
            if rel and alias and alias != rel:
                alias_map[alias] = rel
            for v in node.values():
                collect_aliases(v)
        elif isinstance(node, list):
            for v in node:
                collect_aliases(v)

    collect_aliases(plan)

    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if not any(t in k for t in _PLAN_NOISE_TOKENS)}
        if isinstance(node, list):
            return [strip(v) for v in node]
        if isinstance(node, str):
            for alias, rel in alias_map.items():
                node = re.sub(rf"\b{re.escape(alias)}\.", f"{rel}.", node)
            return node
        return node

    return json.dumps(strip(plan), sort_keys=True)


def _min_improvement_threshold(original_ms: float | None) -> float:
    """Proportional threshold: slow queries need larger improvements to avoid noise."""
    if original_ms is None:
        return 3.0
    if original_ms > 10_000:
        return 10.0
    if original_ms > 1_000:
        return 5.0
    return 3.0


def _time_improvement(original_ms: float | None, optimized_ms: float | None) -> dict:
    if original_ms is None or optimized_ms is None:
        return {"original_ms": original_ms, "optimized_ms": optimized_ms, "delta_ms": None, "improvement_pct": None}
    delta = original_ms - optimized_ms
    pct = round((delta / original_ms) * 100, 2) if original_ms > 0 else 0.0
    return {
        "original_ms": round(original_ms, 3),
        "optimized_ms": round(optimized_ms, 3),
        "delta_ms": round(delta, 3),
        "improvement_pct": pct,
    }



def _robust_explain_analyze(backend, sql: str, timeout_s: int | None = None, uri: str | None = None) -> dict:
    """EXPLAIN ANALYZE with warm-up + median timing — removes cold-start / warm-up-order bias.

    The naive measurement runs original first (cold, reads from disk) and optimized second
    (warm, served from the cache the original just populated). That asymmetry inflates the
    optimized's apparent gain — and can even falsely reject a good rewrite when the original
    happens to be warm and the optimized cold. Here EACH query is warmed once (discarded) then
    timed N times; the median is reported. Both sides end up measured *warm* → fair comparison
    (steady-state, the relevant metric for recurring queries).

    Cost/plan come from the timed runs, so the gates (plan identity, cost) are unaffected.
    Guards: on error, or when even the warm-up run is very slow (big TPC-DS that would make N
    repetitions prohibitive), fall back to the single warm-up result. Configurable via
    TIMING_RUNS (default 3) and TIMING_SLOW_CUTOFF_MS (default 20000).
    """
    runs = int(os.getenv("TIMING_RUNS", "3"))
    slow_cutoff_ms = float(os.getenv("TIMING_SLOW_CUTOFF_MS", "20000"))

    # Warm-up run — ALWAYS discarded. It pays the cold-cache cost so every timed run below is
    # warm. (The cutoff must NOT be checked here: this run is the cold/slow one we discard —
    # e.g. 29c is ~28-41s cold but ~1.8s warm. Checking the cutoff on it would wrongly fall
    # back to the cold number.)
    # ⚠️ ORÇAMENTO PRÓPRIO DO AQUECIMENTO (21/08). O warm-up rodava com o MESMO `timeout_s` das runs
    # cronometradas. Numa query lenta a FRIO mas rápida a QUENTE, ele estoura e a função devolve o
    # erro sem NUNCA chegar às runs quentes — a medição inteira vira limite inferior.
    #   Evidência: a `q69` mediu >45 s nas primeiras runs da sonda de paridade e 722 ms nas últimas
    #   (60×); o comentário abaixo já citava a `29c` em ~28-41 s fria contra ~1,8 s quente.
    # Como o aquecimento é DESCARTADO, o tempo dele não entra em conta nenhuma — dar-lhe um orçamento
    # maior não afeta número reportado algum, só permite que o cache aqueça.
    # ⛔ DEFAULT = comportamento ANTIGO (mesmo timeout). Ligar `WARMUP_TIMEOUT_S` muda o setup de
    #    medição e quebra comparabilidade com o que já rodou — é decisão de congelamento.
    _wt = os.getenv("WARMUP_TIMEOUT_S", "").strip()
    _warm_timeout = int(_wt) if _wt.isdigit() else timeout_s
    warmup = backend.explain_analyze(sql, timeout_s=_warm_timeout, uri=uri)
    if warmup.get("error"):
        return warmup

    samples: list[float] = []
    representative = warmup
    for i in range(runs):
        r = backend.explain_analyze(sql, timeout_s=timeout_s, uri=uri)
        if r.get("error"):
            break
        t = r.get("execution_time_ms")
        if t is not None:
            samples.append(t)
            representative = r
        # Slow even WARM (big TPC-DS) → stop after the first timed run to avoid N×expensive
        # repetitions. The cutoff is checked here, on the first WARM run, not on the warm-up.
        if i == 0 and (t is None or t > slow_cutoff_ms):
            break
    if not samples:
        return warmup

    samples.sort()
    median_ms = samples[len(samples) // 2]
    representative = dict(representative)
    representative["execution_time_ms"] = median_ms
    return representative


def executor(state: PipelineState) -> PipelineState:
    raw_sql = state["raw_sql"]
    optimized_sql = state.get("optimized_sql", "")
    errors: list[str] = []

    if not optimized_sql:
        # DISTINGUIR a causa do "no output" (era ambíguo — custou um debug: architect-timeout × writer-inexistente):
        if state.get("writer_error"):
            _msg = f"writer produced no output — {state['writer_error']}"          # coder falhou (ex.: modelo inexistente/conexão)
        elif state.get("architect_timed_out"):
            _msg = "architect timed out — no output (reasoning over ARCHITECT_TIMEOUT_S)"
        elif state.get("adherence_rejected"):
            # ADHERENCE_GATE (opt-in): o rewrite desviou da estratégia e foi descartado — a causa REAL
            # precisa chegar ao retry (senão vira o genérico "no output" e o coder não sabe o porquê).
            _msg = ("rewrite DEVIATED from the architect's strategy and was discarded — implement THE "
                    "strategy's technique, do not substitute a different optimization")
        else:
            _msg = "optimizer produced no output (empty or unparseable response)"   # LLM respondeu mas sem SQL parseável
        return {**state, "reflection_errors": [_msg], "approved": False, "status": "validation_failed"}

    backend = get_backend()

    # Short-circuit: identical SQL means no_change=true — skip all DB execution.
    # Running EXPLAIN ANALYZE on two identical queries only produces timing noise and
    # risks triggering a spurious "execution time regression" error.
    if optimized_sql.strip() == raw_sql.strip():
        return {
            **state,
            "validation_result": None,
            "reflection_errors": [],
            "approved": True,
            "status": "approved",
            "retry_hint": _backend_hint(
                backend, "hint_no_optimization",
                "no_change=true — no optimization was found. Try harder: attempt a structurally "
                "different rewrite, or return no_change: true if nothing applies.",
            ),
        }

    # Duplicate detection — if this SQL was already rejected, the model is looping.
    # Skip all validation and escalate immediately rather than wasting DB round-trips.
    rejected = state.get("rejected_sql_attempts") or []
    if optimized_sql.strip() in rejected:
        return {
            **state,
            "reflection_errors": ["duplicate_attempt — identical SQL was already rejected this session"],
            "approved": False,
            "status": "validation_failed",
            "retry_hint": (
                "You already submitted this exact SQL and it was rejected. "
                "Do not repeat the same rewrite. If you have no new approach, "
                "return the original query unchanged (no_change: true)."
            ),
            "rejected_sql_attempts": rejected,
        }

    # A. Syntax validation — fast static check before any DB round-trip
    valid, syntax_error = backend.validate_syntax(optimized_sql)
    if not valid:
        return {
            **state,
            "reflection_errors": [f"Syntax error: {syntax_error}"],
            "approved": False,
            "status": "validation_failed",
        }

    # A. Structural integrity — ADVISORY, not a gate. check_structural_integrity is a cheap textual/AST
    # proxy that FALSE-REJECTS valid restructurings which re-encode predicates as STRUCTURE (TPC-DS q11:
    # year_total split into per-(sale_type,year) CTEs → S=1 confirmed equivalent, yet the filter check
    # flagged every predicate "removed"). The equivalence authority is S=1 (execution + EXACT cardinality
    # cross-check), NEVER this proxy — a brittle proxy must not override the stronger judge. So: compute
    # the findings but DO NOT early-return — let the rewrite reach S=1. The findings resurface below as
    # reflection hints IF S=1/execution rejects the rewrite, and as a transparent caveat if S=1 approves
    # it (empirically equivalent on the instance, structurally flagged). See fixed_bugs.md #6.
    struct_errors = backend.check_structural_integrity(raw_sql, optimized_sql)

    # Presentation contract — re-append any top-level ORDER BY/LIMIT/OFFSET the rewrite dropped.
    # The result-hash gate normalizes these away, so a dropped LIMIT/ORDER BY passes silently;
    # restore them here (before timing/serving) so the optimized query honours the user's contract.
    presentation_notes: list[str] = []
    _repair = getattr(backend, "repair_presentation_clauses", None)
    if _repair is not None:
        repaired_sql, presentation_notes = _repair(raw_sql, optimized_sql)
        if presentation_notes:
            optimized_sql = repaired_sql

    # Reference check — optimized must not introduce source tables absent from the original
    original_refs = backend.extract_references(raw_sql)
    optimized_refs = backend.extract_references(optimized_sql)
    extra_refs = optimized_refs - original_refs
    if extra_refs:
        errors.append(f"Optimized query references unknown sources: {extra_refs}")

    # B. Integrity Gatekeeper (S=1) — result hash comparison on LIMIT 100 samples.
    # The ORIGINAL is invariant across reflection attempts — memoize its (slow) measurement
    # once per run instead of re-running it (e.g. q1's ~240s) on every retry.
    orig_cache = state.get("original_measure_cache")
    if orig_cache is not None:
        original_hash, orig_err = orig_cache["hash"], orig_cache["err"]
    else:
        original_hash, orig_err = backend.sample_hash(raw_sql)

    # If the ORIGINAL itself times out, a valid rewrite must beat it FAST — a rewrite that also
    # crawls didn't solve the complexity. Give the optimized a short budget so non-decorrelating
    # attempts are rejected quickly instead of burning the full timeout (q1: 240s → ~30s).
    orig_timed_out = orig_err is not None and backend.is_timeout_error(orig_err)
    opt_timeout = int(os.getenv("OPTIMIZED_FAST_TIMEOUT_S", "30")) if orig_timed_out else None

    optimized_hash, opt_err = backend.sample_hash(optimized_sql, timeout_s=opt_timeout)

    if orig_err:
        if not backend.is_timeout_error(orig_err):
            errors.append(f"Original query execution error: {orig_err}")
    optimized_timed_out = False
    if opt_err:
        opt_error_msg = f"Optimized query execution error: {opt_err}"
        # SQL-specific diagnostics — optional per backend (graph/document engines don't have joins).
        _implicit = getattr(backend, "implicit_join_hint", None)
        implicit_hint = _implicit(raw_sql, opt_err) if _implicit is not None else None
        if implicit_hint:
            opt_error_msg += f"\n{implicit_hint}"
        _window = getattr(backend, "window_filter_hint", None)
        window_hint = _window(optimized_sql, opt_err) if _window is not None else None
        if window_hint:
            opt_error_msg += f"\n{window_hint}"
        # Generic SQL-correctness scaffolding (clause order / double WHERE / per-group avg) — mechanical,
        # no benchmark identifier, no technique named. Helps the model write VALID SQL for the technique IT
        # chose (FIND stays unaided; only the WRITE is scaffolded — see metodologia §0).
        _mech = getattr(backend, "mechanical_write_hint", None)
        mech_hint = _mech(optimized_sql, opt_err) if _mech is not None else None
        if mech_hint:
            opt_error_msg += f"\n{mech_hint}"
        errors.append(opt_error_msg)
        optimized_timed_out = backend.is_timeout_error(opt_err)

    integrity_ok = None
    if original_hash and optimized_hash:
        integrity_ok = original_hash == optimized_hash
        if not integrity_ok:
            integ_msg = _backend_hint(
                backend, "hint_integrity_failure",
                "Integrity check failed: result hashes differ (S≠1) — your rewrite returns different "
                "rows than the original. Preserve the exact result set, or return the original UNCHANGED.",
            )
            _mech = getattr(backend, "mechanical_write_hint", None)
            mh = _mech(optimized_sql, "integrity hashes differ") if _mech is not None else None
            if mh:
                integ_msg += f"\n{mh}"
            # OPT-IN richer feedback: a row-level diff (engine's truth, not the technique) so a same-count
            # content divergence — where "hashes differ" alone is too vague — becomes actionable. Gated by
            # the flag AND skipped when the original didn't sample cleanly (orig_err). Never raises.
            if _rich_equivalence_feedback() and not orig_err:
                diff = _equivalence_diff(backend, raw_sql, optimized_sql, opt_timeout)
                if diff:
                    integ_msg += f"\n{diff}"
            errors.append(integ_msg)

    # C. Physical homologation — actual execution time + planner cost.
    # Warm-up + median per query (warm-vs-warm): removes the cold-start/warm-up-order bias
    # where the original ran cold and the optimized warm. See _robust_explain_analyze.
    if orig_cache is not None:
        original_explain = orig_cache["explain"]
    elif orig_timed_out:
        # The original's sample_hash hit the timeout, so EXPLAIN ANALYZE (which executes) would only burn
        # another budget to re-confirm it. BUT a plain EXPLAIN (cost-only, NO analyze) is INSTANT and
        # captures the planner's COST estimate — needed for the cost≠time finding on these heavy queries
        # (the timeout floor handles TIME). Without it, planner_cost.original stayed null and the run was
        # DROPPED from cost_vs_time (silently losing the biggest wins, e.g. q1/q30). Cost-only never times
        # out; on failure it returns an error and original_cost falls back to None (old behaviour).
        cost_only = backend.explain(raw_sql)
        original_explain = {**cost_only, "execution_time_ms": None}
    else:
        original_explain = _robust_explain_analyze(backend, raw_sql)
    optimized_explain = _robust_explain_analyze(backend, optimized_sql, timeout_s=opt_timeout)

    # Persist the original's measurement so later reflection attempts reuse it (set in the result).
    original_measure_cache = orig_cache if orig_cache is not None else {
        "hash": original_hash, "err": orig_err, "explain": original_explain,
    }

    original_cost = backend.get_total_cost(original_explain)
    optimized_cost = backend.get_total_cost(optimized_explain)
    timing = _time_improvement(original_explain.get("execution_time_ms"), optimized_explain.get("execution_time_ms"))

    # Cardinality cross-check (augments S=1). The LIMIT-100 sample hash only sees the head of a >100-row
    # result, so a row-count divergence beyond the sampled rows can slip through. The EXPLAIN ANALYZE we
    # just ran carries the top-node ACTUAL output rows (true cardinality) for free — if both sides have it
    # and they differ, the rewrite is NOT equivalent even when the sampled hashes matched. Skipped when
    # either side lacks actual rows (original timed out, or engine doesn't expose them) → sample stands.
    # A count is metadata, not row DATA — safe to surface (and it's a SEMANTIC, not mechanical, failure,
    # so it routes to architect reflection, never the corrector).
    orig_rows = backend.top_node_actual_rows(original_explain)
    opt_rows = backend.top_node_actual_rows(optimized_explain)
    if orig_rows is not None and opt_rows is not None and orig_rows != opt_rows:
        integrity_ok = False
        errors.append(
            f"Integrity check failed: the rewrite returns a DIFFERENT number of rows "
            f"({opt_rows} vs {orig_rows} in the original) — S≠1, not an equivalent result set. "
            f"Preserve the exact rows, or return the original UNCHANGED."
        )

    # Plan identity gate — if the optimized plan is structurally identical to the original,
    # the planner normalized the rewrite away (e.g. PostgreSQL inlines non-MATERIALIZED CTEs).
    # Any measured time delta between the two runs is execution noise, not optimization:
    # classify as no_change instead of approving (or rejecting) noise.
    sig_original = _plan_signature(original_explain.get("plan"))
    sig_optimized = _plan_signature(optimized_explain.get("plan"))
    if not errors and sig_original is not None and sig_original == sig_optimized:
        return {
            **state,
            "validation_result": {
                "ast_valid": valid,
                "original_refs": list(original_refs),
                "optimized_refs": list(optimized_refs),
                "original_hash": original_hash,
                "optimized_hash": optimized_hash,
                "integrity_ok": integrity_ok,
                "original_cost": original_cost,
                "optimized_cost": optimized_cost,
                "cost_reduced": True,
                "plans_identical": True,
                "execution_time": {
                    "original_ms": timing["original_ms"],
                    "optimized_ms": timing["optimized_ms"],
                    # measured delta is noise between two runs of the same plan
                    "delta_ms": 0.0,
                    "improvement_pct": 0.0,
                },
                "plans": {
                    "original": original_explain.get("plan"),
                    "optimized": optimized_explain.get("plan"),
                },
            },
            "reflection_errors": [],
            "approved": True,
            "status": "approved",
            "retry_hint": _backend_hint(
                backend, "hint_plan_identical",
                "your rewrite produced an execution plan identical to the original — the engine "
                "normalized the change away, so there is no effective optimization. Try a structurally "
                "different approach. If nothing applies, return no_change: true.",
            ),
            "rejected_sql_attempts": rejected,
        }

    # Conservative LOWER BOUND on the improvement when the full-scale original TIMED OUT (no point
    # estimate is possible). Computed HERE — BEFORE the cost gate — because the cost gate must KNOW the
    # rewrite is actually FASTER (via this lower bound) to avoid vetoing it on a planner-cost increase:
    # the MySQL planner over-estimates a decorrelated rewrite's cost ~3000× while it runs 90%+ faster
    # (q1 cost +305,000% / time +91%). Placing it after the gate (the old bug) left `timing` without the
    # lower bound at gate time → the gate read improvement_pct=None and vetoed a valid rewrite. Retracted
    # below if the smaller-scale instance later PROVES the rewrite wrong. See fixed_bugs.md #7/#8.
    # ⚠️ REQUIRES the optimized to have actually COMPLETED (not optimized_timed_out). On a slow engine
    # (MySQL) the OPTIMIZED also times out → EXPLAIN ANALYZE returns the timeout value as its
    # execution_time_ms (~OPTIMIZED_FAST_TIMEOUT_S), a FLOOR, not a measurement. Comparing that floor
    # (30s) against the original's floor (EXPLAIN_ANALYZE_TIMEOUT_S=45s) yields a BOGUS lower bound
    # (33%) with no real gain behind it — the false-land that made q7/q38/q69 "land" on MySQL with
    # opt_ms≈timeout. When the optimized timed out there is NO valid lower bound → leave it None so the
    # SF1-perf fallback (below) measures the real gain on the verification instance, or it stays no_gain.
    if (orig_timed_out and not optimized_timed_out and integrity_ok is not False
            and timing.get("optimized_ms") is not None):
        floor_ms = float(os.getenv("EXPLAIN_ANALYZE_TIMEOUT_S", "120")) * 1000
        # #16b (2026-07-16): `optimized_timed_out` vem do SAMPLE_HASH (LIMIT 100), mas `optimized_ms` vem do
        # EXPLAIN ANALYZE (execução COMPLETA) — os dois estouram INDEPENDENTE. Num engine lento (MySQL) o
        # sample_hash da otimizada PASSA (optimized_timed_out=False) enquanto o EXPLAIN ANALYZE dela ESTOURA
        # → opt_ms vira o PISO do orçamento (opt_timeout, ex.: 30000), NÃO uma medição. Comparar esse piso
        # contra o floor do original (45000) fabrica um ganho fantasma (33,3% = (45−30)/45) — o piso-vs-piso
        # que o #16 (só olhando optimized_timed_out) deixou passar. O lower-bound só vale se o EXPLAIN ANALYZE
        # da otimizada COMPLETOU (opt_ms abaixo do PRÓPRIO orçamento dela). Senão → sem lb → cai no SF1-perf.
        _opt_ea_budget_ms = (opt_timeout * 1000) if opt_timeout else floor_ms
        _opt_ea_completed = timing["optimized_ms"] < _opt_ea_budget_ms - 500
        if _opt_ea_completed and floor_ms > timing["optimized_ms"]:
            timing["original_floor_ms"] = floor_ms
            timing["improvement_lower_bound_pct"] = round(
                (floor_ms - timing["optimized_ms"]) / floor_ms * 100, 2
            )

    cost_reduced = None
    if original_cost is not None and optimized_cost is not None:
        cost_reduced = optimized_cost <= original_cost
        if not cost_reduced:
            # Cost gate is engine-specific (relational planner cost); optional per backend.
            _cost_gate = getattr(backend, "cost_gate_error", None)
            cost_error = _cost_gate(original_cost, optimized_cost, timing) if _cost_gate is not None else None
            if cost_error:
                errors.append(cost_error)

    # Accumulated across all attempts (never cleared, unlike reflection_errors) — these
    # hints often name the structural limit of the query (e.g. "a trigram/GIN index is
    # required"), which is exactly the signal the index_advisor needs later.
    rewrite_hints: list[str] = list(state.get("rewrite_hints") or [])

    if timing["improvement_pct"] is not None and timing["improvement_pct"] < -0.1:
        regression_msg = f"Execution time regression: {timing['original_ms']}ms → {timing['optimized_ms']}ms"
        causal_hint = backend.causal_regression_hint(optimized_sql)
        if causal_hint:
            regression_msg += f"\n{causal_hint}"
            if causal_hint not in rewrite_hints:
                rewrite_hints.append(causal_hint)
        errors.append(regression_msg)

    # Equivalence re-check on the smaller-scale verification instance (VERIFY_DB_URI, e.g. a TPC-DS
    # SF 0.01 load). Two triggers:
    #  (a) the full-scale original TIMED OUT → no baseline at all; at small scale the O(n²) original
    #      finishes and adjudicates (a wrong rewrite — e.g. a dropped correlation-key join — diverges).
    #  (b) the full-scale check said S≠1 → which is EITHER a genuine non-equivalence OR the full-scale
    #      ORIGINAL being MIS-EXECUTED by the engine at scale. Observed on MySQL: a correlated subquery
    #      over a CTE returns the WRONG rows only on large data (TPC-DS q81 returns 0 at SF20 but the
    #      correct 189 at SF1). The verification instance runs the original CORRECTLY, so it adjudicates:
    #      if original and rewrite MATCH there, the rewrite IS equivalent and the full-scale mismatch was
    #      the engine mis-executing the ORIGINAL (scale divergence) — NOT a bad rewrite. We trust the
    #      instance where the original runs correctly (same trust the timeout path already places in it).
    # Equivalence is a logical property; verifying on a smaller real instance is the same kind of evidence
    # as the LIMIT-100 hash. Performance stays measured at full scale.
    verify_uri = os.getenv("VERIFY_DB_URI")
    scale_divergence = False  # RETIRED as an accept path (see below); kept in the schema for back-compat.
    float_sensitive_sf1 = False
    # SF1 adjudication fires ONLY when the full-scale original has NO baseline (integrity_ok is None —
    # it or the optimized TIMED OUT). We NO LONGER let SF1 OVERTURN a DEFINITIVE full-scale S≠1 (the
    # retired `scale_divergence` accept): if the full-scale original COMPLETED and its result DIFFERED
    # from the rewrite, that verdict STANDS (reject). One authority per run — the scale that completes —
    # never a cross-scale override. The old override FALSE-ACCEPTED q30, whose full-scale mismatch was a
    # REAL float-borderline non-equivalence (`> avg(...)*1.2`) that SF1 — too small to accumulate the
    # divergence — masked as equivalent; PG (where the original completes) correctly rejected it. #17.
    # #12/MySQL: the optimized-timeout case still adjudicates here (orig hash present, opt timed out ⇒
    # integrity_ok is None ⇒ SF1 re-hashes BOTH); only a definitive S≠1 (both hashes, differ) is excluded.
    _opt_verifiable = optimized_hash is not None or optimized_timed_out
    if _opt_verifiable and verify_uri and integrity_ok is None and (orig_timed_out or optimized_timed_out):
        v_budget = int(os.getenv("VERIFY_SAMPLE_HASH_TIMEOUT_S", "60"))
        v_orig_hash, _ = backend.sample_hash(raw_sql, timeout_s=v_budget, uri=verify_uri)
        v_opt_hash, _ = backend.sample_hash(optimized_sql, timeout_s=v_budget, uri=verify_uri)
        if v_orig_hash and v_opt_hash:
            v_equiv = v_orig_hash == v_opt_hash
            integrity_ok = v_equiv  # timeout path: SF1 is the ONLY baseline
            if v_equiv:
                # S=1 established on SF1 despite the full-scale timeout → NOT a mechanics failure. Clear the
                # optimized-timeout error so the run is classified by PERFORMANCE (no_gain / land), not mech.
                errors[:] = [e for e in errors if "Optimized query execution error" not in e]
                # PROVISIONAL flag: for a float-threshold comparison (`> avg/sum ...`), SF1 is too small to
                # exercise the float-accumulation divergence that flips borderline rows at scale — so this
                # S=1 is NOT a full-scale guarantee (q1 holds at scale; its twin q30 does NOT — identical
                # shape, data-dependent outcome). NOT a gate: the land stands, tagged scale-fragile for
                # cross-engine / full-scale adjudication. #17.
                _floatcmp = getattr(backend, "is_float_threshold_comparison", None)
                if _floatcmp is not None and _floatcmp(raw_sql):
                    float_sensitive_sf1 = True
            else:
                errors.append(_backend_hint(
                    backend, "hint_integrity_failure",
                    "Integrity check failed on the smaller-scale verification instance: result hashes "
                    "differ (S≠1) — your rewrite returns different rows than the original. Preserve the "
                    "exact result set, or return the original UNCHANGED.",
                ))

    # PERF FALLBACK on SF1 (2026-07-14): the SAME "timeout → SF1 for a feasible result" rule the EQUIVALENCE
    # already uses, extended to PERFORMANCE. On a slow engine (MySQL) the OPTIMIZED also times out at full
    # scale → no measurable speed-up (improvement_pct None AND no lower-bound) even though S=1 holds. PG
    # never hit this (its rewrite completed at full scale → real time); MySQL does. So when the full-scale
    # speed-up is unmeasurable but S=1 holds, measure BOTH sides on the SF1 verify instance (where they
    # complete) and report the gain THERE, flagged `perf_scale="SF1"`. If SF1 STILL shows no gain, it stays
    # no_gain (the gain-evidence guard below). Only when a verification instance exists.
    if (integrity_ok and verify_uri
            and timing.get("improvement_pct") is None
            and timing.get("improvement_lower_bound_pct") is None):
        v_orig = _robust_explain_analyze(backend, raw_sql, uri=verify_uri)
        v_opt = _robust_explain_analyze(backend, optimized_sql, uri=verify_uri)
        v_timing = _time_improvement(v_orig.get("execution_time_ms"), v_opt.get("execution_time_ms"))
        if v_timing.get("improvement_pct") is not None:
            v_timing["perf_scale"] = "SF1"   # HONESTO: ganho medido na instância de verificação (full-scale estourou)
            timing = v_timing

    # A — equivalence UNVERIFIED: the full-scale original was too slow to hash AND no smaller-scale
    # instance confirmed it (not configured, or the original timed out there too). The rewrite runs
    # with no error, but its equivalence is NOT verified — not a clean approval, not cached.
    integrity_unverified = (
        integrity_ok is None
        and optimized_hash is not None
        and orig_err is not None
        and backend.is_timeout_error(orig_err)
    )

    # The conservative LOWER BOUND was computed ABOVE, before the cost gate (it must inform the gate).
    # Honesty re-check: if the smaller-scale instance later PROVED the rewrite wrong (integrity_ok False),
    # retract it — we never report a speed-up for a rewrite that returns different rows.
    if integrity_ok is False:
        timing.pop("original_floor_ms", None)
        timing.pop("improvement_lower_bound_pct", None)

    validation_result = {
        "ast_valid": valid,
        "original_refs": list(original_refs),
        "optimized_refs": list(optimized_refs),
        "original_hash": original_hash,
        "optimized_hash": optimized_hash,
        "integrity_ok": integrity_ok,
        "integrity_unverified": integrity_unverified,
        "scale_divergence": scale_divergence,  # RETIRED accept path — always False now (schema back-compat)
        "float_sensitive_sf1_only": float_sensitive_sf1,  # PROVISIONAL land: S=1 only on SF1 + `>avg/sum` predicate → scale-fragile
        # → the full-scale ORIGINAL was mis-executed by the engine at scale; equivalence holds.
        "original_cost": original_cost,
        "optimized_cost": optimized_cost,
        "cost_reduced": cost_reduced,
        "execution_time": timing,
        "plans": {
            "original": original_explain.get("plan"),
            "optimized": optimized_explain.get("plan"),
        },
    }

    # Equivalence (S=1) is MANDATORY. If the original timed out, its result set could not be hashed,
    # so equivalence was never checked — and a fast rewrite may be fast precisely because it is WRONG
    # (e.g. a dropped correlation-key join that turns a correlated subquery into a cross join). Without
    # verification we cannot approve: an unverified rewrite is NOT a success. This closes the hole
    # where a too-slow baseline let a wrong rewrite through as `approved` (q1 decorrelation).
    if integrity_unverified:
        errors.append(
            "Equivalence UNVERIFIED — the original query timed out, so its result set could not be "
            "hashed and the S=1 integrity check could not run. A rewrite is not approved unless it is "
            "verified to return the same rows as the original."
        )

    approved = len(errors) == 0

    # Structural proxy as advisor, not judge (see the structural-integrity note above). Decided AFTER
    # S=1/execution so it never gates on its own: if the rewrite did NOT clear S=1/execution, the
    # structural findings join the reflection hints (help the model re-add a genuinely dropped
    # filter/table) and mark the attempt rejected via the keyword scan below. If S=1 PASSED, the rewrite
    # is empirically equivalent → the structural flag was a false alarm on a valid restructuring: keep it
    # only as a transparent caveat in the result, never a blocker.
    if struct_errors:
        if approved:
            validation_result["structural_caveat"] = struct_errors
        else:
            for se in struct_errors:
                if se not in errors:
                    errors.append(se)

    # Track rejected SQLs to detect loops — structural errors and timeouts are deterministic:
    # the same SQL will fail the same way regardless of cache state.
    # Timing regressions are excluded: the same SQL could pass on a warmer buffer cache.
    if not approved:
        structural = optimized_timed_out or any(
            kw in e
            for e in errors
            for kw in ("Cartesian", "Data_Loss", "Syntax", "duplicate_attempt", "Structural_Error")
        )
        if structural:
            rejected = list(rejected)
            rejected.append(optimized_sql.strip())

        # APPLICATION mode only: if a failed rewrite half-applied the technique (e.g. still correlated
        # after a decorrelation rule), enrich the feedback with a TARGETED hint for the REASONING model's
        # next reflection. Routed via reflection_errors (architect), NOT the corrector — it is a
        # non-mechanical hint (is_mechanical_failure stays False), so completing the optimization stays
        # the reasoning model's job, never the code model's. Discovery mode stays neutral (no hint).
        if _app_mode():
            tech_hint = backend.incomplete_technique_hint(raw_sql, optimized_sql)
            if tech_hint and tech_hint not in errors:
                errors.append(tech_hint)

    # Sub-threshold approvals are valid rewrites but their measured gain is below the
    # noise floor the system itself defines for this query duration. They are approved
    # (rejecting would feed the model a misleading error), but they don't become the
    # best result, aren't cached, and therefore aren't claimed as improvement to the DBA.
    improvement_pct = timing.get("improvement_pct") if approved else None
    original_ms = timing.get("original_ms")
    threshold = _min_improvement_threshold(original_ms)
    low_improvement = improvement_pct is not None and improvement_pct < threshold

    # Store in semantic cache only after full Phase B approval — prevents caching invalid
    # rewrites; sub-threshold rewrites aren't worth reusing either.
    if approved and not low_improvement and not integrity_unverified:
        SemanticCache().store(raw_sql, optimized_sql)

    result: dict = {
        **state,
        "optimized_sql": optimized_sql,
        "presentation_repaired": presentation_notes,
        "validation_result": validation_result,
        "reflection_errors": errors if not approved else [],
        "approved": approved,
        "status": "approved" if approved else ("unverified" if integrity_unverified else "validation_failed"),
        "retry_hint": None,
        "rejected_sql_attempts": rejected,
        "rewrite_hints": rewrite_hints,
        "original_measure_cache": original_measure_cache,
    }

    if approved:
        # GAIN-EVIDENCE GUARD (fix 2026-07-14 — MySQL false-lands): a land MUST have EVIDENCE of a speedup —
        # a measured `improvement_pct` OR a lower-bound (original timed out but the rewrite ran fast under
        # the floor). With NEITHER (both original AND rewrite exceed the exec-time budget at full scale →
        # `improvement_pct` None AND no lower-bound), there is NO proof the rewrite is faster → it is NOT a
        # land (becomes no_gain). Before, `improvement_pct=None` slipped through: `low_improvement` only
        # fires for a NUMBER below threshold, and `new_pct` defaulted to 0.0 → best_approved_sql was set →
        # `rewrite_correct` with no gain (the whole MySQL run showed 10/21 hollow lands, opt_ms≈timeout).
        _lb = timing.get("improvement_lower_bound_pct")
        gain_pct = improvement_pct if improvement_pct is not None else _lb
        has_gain_evidence = gain_pct is not None
        new_pct = gain_pct if gain_pct is not None else 0.0
        best_pct = state.get("best_improvement_pct", -999.0)

        # Track best result across all retry attempts — only gains WITH EVIDENCE, above the noise floor.
        if has_gain_evidence and not low_improvement and (state.get("best_approved_sql") is None or new_pct > best_pct):
            result["best_approved_sql"] = optimized_sql
            result["best_improvement_pct"] = new_pct
            result["best_validation_result"] = validation_result

        # Signal a quality retry — sub-threshold gain, OR (new) equivalent-but-unmeasurable-at-scale.
        if not has_gain_evidence:
            result["retry_hint"] = (
                "the rewrite is EQUIVALENT (S=1) but NO speedup could be measured — both the original and "
                "the rewrite exceeded the execution-time budget at full scale, so there is no proof it is "
                "faster. NOT counted as an improvement. Try a rewrite that completes well under the budget."
            )
        elif low_improvement:
            result["retry_hint"] = (
                f"improvement was only {improvement_pct:.1f}% — below the {threshold:.0f}% target "
                f"for queries running ~{int(original_ms)}ms, which is within measurement noise. "
                "This attempt will not be reported as an improvement. "
                "Try a more aggressive optimization: different join order anchor, "
                "more CTEs for heavily filtered tables, or reconsider which table is the bottleneck."
            )

    return result
