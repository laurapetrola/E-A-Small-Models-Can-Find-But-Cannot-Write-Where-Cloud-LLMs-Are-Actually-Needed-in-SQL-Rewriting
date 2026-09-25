from typing import NotRequired, TypedDict


class PipelineState(TypedDict):
    raw_sql: str
    status: str
    schema_context: NotRequired[dict]
    hardware_snapshot: NotRequired[dict]
    system_prompt: NotRequired[str]
    explain_plan: NotRequired[dict]
    optimized_sql: NotRequired[str]
    cache_hit: NotRequired[bool]
    reflection_errors: NotRequired[list[str]]
    reflection_iterations: NotRequired[int]
    validation_result: NotRequired[dict]
    approved: NotRequired[bool]
    hw_override: NotRequired[dict]  # optional: inject hardware hints for controlled experiments
    retry_attempts: NotRequired[int]      # quality-based retries (separate from error reflection)
    corrector_attempts: NotRequired[int]  # code-model SQL repairs this run (class-b fallback, env-gated CORRECTOR_ENABLED)
    retry_hint: NotRequired[str | None]   # set by executor when approved but improvement is weak
    best_approved_sql: NotRequired[str]       # best result across all attempts (by improvement_pct)
    best_improvement_pct: NotRequired[float]
    best_validation_result: NotRequired[dict] # validation metrics for best_approved_sql
    inference_ms: NotRequired[float]               # cumulative LLM inference time across all architect calls
    index_advisor_inference_ms: NotRequired[float] # LLM inference time for the index_advisor node
    # WRITER COST/LATENCY — the corrector computes these, but until 2026-08-15 they were NOT declared
    # here, so LangGraph dropped them between nodes and the store recorded 0.0 (same failure mode as
    # architect_timed_out and decomposed_provenance below). Consequence: `total_inference_ms` summed
    # ONLY the local components, and NO cost data existed at all — which is why the pro-vs-flash writer
    # ablation had to be decided by a standalone benchmark. Declared so future cells carry both.
    writer_inference_ms: NotRequired[float]   # cumulative writer (coder) latency, accumulated across retries
    writer_tokens_in: NotRequired[int]        # cumulative prompt tokens billed to the writer
    writer_tokens_out: NotRequired[int]       # cumulative completion tokens billed to the writer
    writer_calls: NotRequired[int]            # number of writer API calls (tokens/call is the per-call cost)
    suggestions: NotRequired[list[str]]   # advisory observations from the LLM, accumulated across all attempts
    used_fallback: NotRequired[bool]      # True if the rewrite came from the free-form path (no decomposed structure)
    original_measure_cache: NotRequired[dict]  # memoized {hash, err, explain} of the ORIGINAL — invariant across reflection attempts, computed once per run
    rewrite_hints: NotRequired[list[str]] # executor's causal hints (e.g. non-sargable → GIN required), accumulated; fed to index_advisor
    rejected_sql_attempts: NotRequired[list[str]]  # SQLs already rejected this session — detect loops (structural only)
    attempt_history: NotRequired[list[str]]  # every distinct non-empty rewrite attempt (any failure type) — for the B->A discovery feed
    rules_in_prompt: NotRequired[list[str]]  # PROVENANCE: learned-rule names injected into the rewrite prompt (arch B); [] in discovery mode — makes rule attribution auditable
    reasoning_trace: NotRequired[str]        # raw model output of the last free-form attempt (incl. <think>) — transparency + corroborates rule use (NOT proof; ablation is)
    architect_timed_out: NotRequired[bool]   # architect single-call hit ARCHITECT_TIMEOUT_S → outcome="timeout" in input_router. MUST be declared or LangGraph drops it → timeouts misclassified as mechanics_failed
    decomposed_provenance: NotRequired[dict] # {transform, trigger, suggestions} provenance — MUST be declared or it arrives empty at the saver
    from_corrector: NotRequired[bool]        # the corrector (not the model) produced the approved SQL — MUST be declared or corrector_rescued always reads False (matters when the corrector is wired)
    # 2-AGENTES (TWO_AGENT_MODE): o architect DECIDE a estratégia (não escreve SQL) e o writer ESCREVE dela.
    strategy: NotRequired[str]               # a estratégia NL-estruturada do architect (o FIND no modo 2-agentes); persistida mesmo no b→b
    strategy_attempts: NotRequired[int]      # quantas estratégias DIFERENTES o architect tentou (o FIND itera — Loop 2)
    # #12 validated-strategy cache — MUST ser declarado ou o LangGraph dropa entre o architect e o input_router
    cached_strategy_id: NotRequired[str | None]   # id da entrada do cache reusada no bypass (o hook do router reforça/rebaixa por ele)
    used_strategy_cache: NotRequired[bool]        # STICKY: algum passe usou o cache (bypass) → strategy_cache_hit na resposta (curva)
    writer_used: NotRequired[str]            # QUEM escreveu o SQL aprovado: "local:<model>" | "cloud:<model>" — atribuição no store
    adherence: NotRequired[dict | None]      # juiz de aderência (WRITER_ADHERENCE_CHECK): {verdict, judge_note} — o writer seguiu a técnica do FIND?
    index_specs: NotRequired[list[dict]]         # Hypothetical_Index_Spec list from index_advisor
    index_reasoning_trace: NotRequired[str]      # CoT of the index_advisor (incl. <think>) — MUST be declared or LangGraph drops it before save
    index_workload_aware: NotRequired[bool]      # generation FORM (per-query vs workload-aware) — MUST be declared or it defaults to False on save (Form 1/2 collide)
    simulation_reports: NotRequired[list[dict]]  # Simulation_Report list from index_simulation_validator
    persuasion_report: NotRequired[dict]         # human-readable index recommendations from persuasion_layer
