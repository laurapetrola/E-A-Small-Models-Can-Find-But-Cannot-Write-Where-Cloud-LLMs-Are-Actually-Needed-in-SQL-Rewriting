"""CORRECTOR node (Direction: WRITE-axis repair at inference).

When the reasoning model FOUND the optimization (suggestion) but wrote INVALID output (class b —
alias mismatch, dropped table, scope error, column typo), a CODE model repairs it, PRESERVING the
rewrite's optimization strategy. It REPAIRS, it does not OPTIMIZE: the S=1 gate enforces equivalence,
and the prompt forbids changing the strategy or reverting to the original.

It is a FALLBACK: only runs on a MECHANICAL rejection (backend.is_mechanical_failure). If the
reasoning model already produced valid output (class a), the corrector is never reached. Env-gated
(CORRECTOR_ENABLED) so it is OFF by default — enabling it is a substantive pipeline change.

PARADIGM-AGNOSTIC: this node is pure orchestration (env-gate, attempt counting, call the coder, hand
back / fail-open). The engine-specific knowledge — the specialist corrector PROMPT, the mechanical-
failure markers, the error sanitization — lives on the BACKEND (build_corrector_prompt,
is_mechanical_failure, sanitize_corrector_errors). A future NoSQL backend plugs in its own specialist
corrector prompt without touching this node.

Fail-open: any error leaves the state untouched for the normal reflection loop.
"""
import logging
import os
import re

from src.pipeline.backends import get_backend
from src.pipeline.state import PipelineState

log = logging.getLogger(__name__)


def corrector_enabled() -> bool:
    return os.getenv("CORRECTOR_ENABLED", "").strip().lower() in ("1", "true", "on", "yes")


def _norm(sql: str) -> str:
    """Normalize for the revert check: a repair that only RE-FORMATS the original (case, whitespace,
    newlines) is still a revert, not a repair — exact string match would miss it (observed on q9: the
    coder reverted to the original in UPPERCASE/multi-line and slipped through as a 0%-gain 'rescue')."""
    return re.sub(r"\s+", " ", (sql or "")).strip().rstrip(";").lower()


def _format_history(history: list[str], broken: str) -> str:
    """Show prior REJECTED rewrites (text only) so the coder does not reproduce a dead end.
    Paradigm-agnostic: just prior attempt strings, no engine-specific parsing."""
    prior = [h for h in (history or []) if h and h.strip() and h.strip() != (broken or "").strip()]
    if not prior:
        return ""
    block = "\n\n".join(p.strip() for p in prior[-2:])  # bound context to the 2 most recent
    return f"\nPRIOR ATTEMPTS (already rejected — do NOT reproduce these):\n{block}\n"


def two_agent_mode() -> bool:
    """TWO_AGENT_MODE: o architect DECIDE a estratégia (não escreve SQL) e o writer (este nó) ESCREVE dela.
    OFF (default) = RAW (1 agente, architect escreve o SQL) — baseline intacto."""
    return os.getenv("TWO_AGENT_MODE", "").strip().lower() in ("1", "true", "on", "yes")


def _writer_which() -> str:
    """Qual writer: 'local' (produto, ollama) | 'cloud' (teto/oráculo, API). Backward-compat: se
    FORMALIZER_API_BASE estiver setado (toggle antigo da sonda), força cloud."""
    if os.getenv("FORMALIZER_API_BASE"):
        return "cloud"
    return os.getenv("WRITER_BACKEND", "local").strip().lower()


def _coder_llm(which: str | None = None):
    """The CODE model. Seletor WRITER_BACKEND: local (qwen2.5-coder via ollama) | cloud (deepseek via API).
    Backward-compat com o toggle antigo (FORMALIZER_API_BASE/CORRECTOR_MODEL)."""
    which = which or _writer_which()
    if which == "cloud":
        from langchain_openai import ChatOpenAI
        model = os.getenv("CLOUD_CODER_MODEL") or os.getenv("CORRECTOR_MODEL") or "deepseek-chat"
        base = os.getenv("CLOUD_CODER_API_BASE") or os.getenv("FORMALIZER_API_BASE")
        # WRITER_REASONING=off disables the cloud writer's internal chain of thought.
        #
        # WHY IT MATTERS (measured 2026-08-16): deepseek-v4-pro is a REASONING model and the reasoning
        # is ~90% of what we are billed — 20,438 output tokens median per call, of which only ~300 are
        # the SQL we actually use; the rest arrives in `reasoning_content` and is DISCARDED. Turning it
        # off cuts output tokens ~40x and latency ~60x (494s -> 4s on real payloads).
        #
        # WHY IT SHOULD BE FREE, ARCHITECTURALLY: in this design the writer DECIDES NOTHING — the
        # architect already chose the strategy; the writer transcribes it into SQL. If its reasoning is
        # redundant by construction, disabling it costs no quality. That is a HYPOTHESIS, and it is
        # measured by a paired cell, never assumed: the SQL does come out different (8/8 payloads), and
        # only the S=1 gate can say whether "different" means "worse".
        #
        # Default ON, so every cell measured before this date keeps its exact behaviour.
        extra = {}
        if os.getenv("WRITER_REASONING", "on").strip().lower() in ("0", "false", "off", "no"):
            extra = {"thinking": {"type": "disabled"}}
        return ChatOpenAI(model=model, base_url=base,
                          api_key=os.getenv("KEY_DEEPSEEK") or os.getenv("FORMALIZER_API_KEY") or "",
                          temperature=0, extra_body=extra or None)
    from langchain_ollama import ChatOllama
    model = os.getenv("LOCAL_CODER_MODEL") or os.getenv("CORRECTOR_MODEL") or "qwen2.5-coder:7b"
    # WRITER_REASONING also applies to the LOCAL writer (added 2026-08-18).
    #
    # WHY: disabling reasoning on the CLOUD writer was catastrophic — reach 14 -> 8 and 9
    # non-equivalences where there had been zero. The default local writer, qwen2.5-coder:7b, has NO
    # reasoning at all, and it fails mechanically ~71% of the time. So the obvious explanation ("it
    # fails because it is small") may be wrong: it may fail because it does not REASON.
    #
    # The toggle makes that testable: qwen3:8b (a reasoning model of the SAME size class) as writer,
    # against qwen2.5-coder:7b (a code model without reasoning). Same size, so the variable is
    # REASONING, not capacity. Ollama treats think:false as the default on non-thinking models, so
    # passing it is safe either way.
    kwargs = {"model": model, "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
              "temperature": 0}
    _r = os.getenv("WRITER_REASONING", "").strip().lower()
    if _r in ("0", "false", "off", "no"):
        kwargs["reasoning"] = False
    elif _r in ("1", "true", "on", "yes"):
        kwargs["reasoning"] = True
    return ChatOllama(**kwargs)


def writer_label(which: str | None = None) -> str:
    """'local:<model>' | 'cloud:<model>' — gravado no store (QUEM escreveu a técnica)."""
    which = which or _writer_which()
    if which == "cloud":
        # The reasoning regime goes IN THE LABEL: two cells with the same model but different
        # WRITER_REASONING are different experiments and must not be indistinguishable in the store.
        _nothink = "" if os.getenv("WRITER_REASONING", "on").strip().lower() not in ("0", "false", "off", "no") else "+nothink"
        return f"cloud:{os.getenv('CLOUD_CODER_MODEL') or os.getenv('CORRECTOR_MODEL') or 'deepseek-chat'}{_nothink}"
    # The reasoning regime goes in the label here too: three writer arms (code-no-reasoning,
    # code-same-family, reasoning) must be distinguishable in the store.
    _r = os.getenv("WRITER_REASONING", "").strip().lower()
    _suf = "+think" if _r in ("1", "true", "on", "yes") else ("+nothink" if _r in ("0", "false", "off", "no") else "")
    return f"local:{os.getenv('LOCAL_CODER_MODEL') or os.getenv('CORRECTOR_MODEL') or 'qwen2.5-coder:7b'}{_suf}"


def corrector(state: PipelineState) -> PipelineState:
    raw_sql = state["raw_sql"]
    broken = state.get("optimized_sql", "")
    errors = state.get("reflection_errors", [])
    attempts = state.get("corrector_attempts", 0) + 1
    # PRESERVE the model's pre-repair attempt (the technique it REACHED) in attempt_history BEFORE the
    # corrector overwrites optimized_sql. Otherwise a reached-but-botched technique that the corrector
    # repairs/reverts is LOST from the Direction B→A discovery feed: the architect only logs attempts
    # when IT re-runs, which never happens if the repaired SQL is approved → END (e.g. TPC-DS q9 — the
    # model reached the scan-consolidation, botched the SQL, the corrector reverted, and the FIND signal
    # vanished). Dedup + skip the no-op (broken == original).
    hist = list(state.get("attempt_history") or [])
    _b = (broken or "").strip()
    if _b and _b != (raw_sql or "").strip() and _b not in (h.strip() for h in hist):
        hist.append(broken)
    import time as _time
    # TEMPO DO WRITER (gap achado 2026-08-07): o coder não era cronometrado — total_inference subcontava o
    # "pensamento total". Acumula no state (writer_inference_ms) através dos retries; input_router persiste.
    _writer_ms = float(state.get("writer_inference_ms") or 0.0)
    # WRITER COST (2026-08-15): the provider returns token usage on every response and we were throwing
    # it away, so "is the cheaper writer actually cheaper?" had NO answer from our own data — only the
    # provider's price list. Accumulated across retries exactly like the latency above.
    _tok_in = int(state.get("writer_tokens_in") or 0)
    _tok_out = int(state.get("writer_tokens_out") or 0)
    _calls = int(state.get("writer_calls") or 0)

    def _bill(resp):
        """Add one writer response to the running cost. Never raises: cost telemetry must not be able
        to break a rewrite. Local backends report nothing, and that is recorded faithfully as zero."""
        nonlocal _tok_in, _tok_out, _calls
        _calls += 1
        try:
            u = getattr(resp, "usage_metadata", None) or {}
            if u:
                _tok_in += int(u.get("input_tokens") or 0)
                _tok_out += int(u.get("output_tokens") or 0)
                return
            u = (getattr(resp, "response_metadata", None) or {}).get("token_usage") or {}
            _tok_in += int(u.get("prompt_tokens") or 0)
            _tok_out += int(u.get("completion_tokens") or 0)
        except Exception:
            pass
    writer_error = None  # captura a CAUSA de um repair vazio (ex.: modelo do coder inexistente) — senão vira o genérico "no output"
    try:
        from langchain_core.messages import HumanMessage
        which = _writer_which()
        llm = _coder_llm(which)
        backend = get_backend()
        history = _format_history(state.get("attempt_history", []), broken)
        if two_agent_mode() and state.get("strategy"):  # GERAR: escreve do ZERO a partir da estratégia do architect
            # schema-linking (opt-in SCHEMA_LINKING): dá as colunas REAIS das tabelas → writer não alucina coluna
            _sl_on = os.getenv("SCHEMA_LINKING", "").strip().lower() in ("1", "true", "on", "yes")
            _schema = backend.relevant_schema_block(raw_sql) if _sl_on else ""
            # linter de auto-consistência (opt-in WRITER_LINT): rejeita CTE-fantasma / coluna fora de escopo /
            # ambígua ANTES de gastar execução e devolve a msg cirúrgica pro próximo prompt (loop interno bounded)
            _lint_on = os.getenv("WRITER_LINT", "").strip().lower() in ("1", "true", "on", "yes")
            # pré-check formal `[4]` (opt-in SEMANTIC_PRECHECK): compara ORIGINAL × reescrita e barra as
            # não-equivalências PROVÁVEIS (LIMIT interno / join derrubado / auto-join colapsado) ANTES de
            # gastar execução. Validado offline contra 126 runs reais: pega 7/17 equiv-fail com ZERO
            # falso-positivo nos 21 lands. Compartilha o loop bounded do lint (mesma mensagem cirúrgica).
            _pc_on = os.getenv("SEMANTIC_PRECHECK", "").strip().lower() in ("1", "true", "on", "yes")
            _lint_max = int(os.getenv("WRITER_LINT_MAX", "2")) if (_lint_on or _pc_on) else 0
            # MASKING (opt-in, CLOUD leg only — P2/RQ5 Gate 3): the cloud coder gets a bijectively
            # masked payload (no real identifiers/string literals); we UNMASK its output before the
            # S=1 gate sees it. FIND(architect) is LOCAL/trusted → untouched. Everything inside the
            # loop stays in masked-space (lint/precheck compare masked×masked, consistent); we unmask
            # ONCE on exit so downstream (technique_preserved, executor, S=1) sees the REAL SQL.
            from src.pipeline import masking as _mask
            _mm = None
            _o_sql, _o_strat, _o_schema, _o_hist = raw_sql, state["strategy"], _schema, history
            if which == "cloud" and _mask.masking_enabled():
                _dialect = backend.sqlglot_dialect()
                _mm = _mask.build_mask_map(raw_sql, dialect=_dialect)
                # Schema-linking advertises EVERY column of each relevant table, not just the ones the
                # query touches. Those extra names were absent from the map built from the SQL, so they
                # travelled to the cloud UNMASKED (median 85 per payload, 21/21 payloads) and find_leaks
                # could not see them — it only checks names that are in the map. Extend the map with the
                # block's identifiers BEFORE masking it: closes the leak and restores the guarantee.
                if _schema:
                    _mask.extend_mask_map(_mm, _mask.identifiers_in_schema_block(_schema))
                _o_sql = _mask.mask_sql(raw_sql, _mm, dialect=_dialect)
                _o_strat = _mask.mask_text(state["strategy"], _mm)
                _o_schema = _mask.mask_text(_schema, _mm)
                _o_hist = _mask.mask_text(history, _mm)
            log.info(f"[corrector] GENERATE | writer={writer_label(which)} | schema_linking={'ON' if _sl_on else 'off'} ({len(_schema)} chars) | lint={'ON' if _lint_on else 'off'} | precheck={'ON' if _pc_on else 'off'}"
                     + (f" | MASKING={_mask.mask_scope()} (ids={len(_mm.ids)} strs={len(_mm.strs)} nums={len(_mm.nums)})" if _mm else ""))
            _errs = [_mask.mask_text(e, _mm) for e in errors] if _mm else list(errors)
            repaired = None
            for _li in range(_lint_max + 1):
                prompt = backend.build_generate_prompt(original=_o_sql, strategy=_o_strat, errors=_errs, history=_o_hist, schema_context=_o_schema)
                if _mm is not None:  # non-leak guarantee (peça c): the payload carries only structure
                    _leaks = _mask.find_leaks(prompt, _mm)
                    if _leaks:
                        log.warning(f"[corrector] MASKING LEAK — real tokens in cloud payload: {_leaks[:8]}")
                _t0 = _time.monotonic()
                resp = llm.invoke([HumanMessage(content=prompt)])
                _writer_ms += (_time.monotonic() - _t0) * 1000.0
                _bill(resp)
                repaired = backend.extract_query(resp.content)
                if not (_lint_on or _pc_on) or not repaired:
                    break
                _ok, _msg, _kind = True, "", ""
                if _lint_on:
                    _ok, _msg = backend.lint_self_consistency(repaired)
                    _kind = "SELF-CONSISTENCY"
                if _ok and _pc_on:
                    _ok, _msg = backend.precheck_semantic_preservation(_o_sql, repaired)  # masked×masked (consistente)
                    _kind = "EQUIVALENCE-PRESERVATION"
                if _ok:
                    break
                log.info(f"[corrector] {_kind} reject (try {_li+1}/{_lint_max+1}): {_msg[:90]}")
                _base_errs = [_mask.mask_text(e, _mm) for e in errors] if _mm else list(errors)  # fica em espaço-mascarado
                _errs = _base_errs + [f"{_kind} error in your previous rewrite — you MUST fix it: {_msg}"]
            if _mm is not None and repaired:  # sai do espaço-mascarado: o S=1/executor valida o SQL REAL
                repaired = _mask.unmask(repaired, _mm)
        else:  # CORRIGIR: patcheia o botch (B1) — prompt engine-specific + sanitização ficam no backend
            prompt = backend.build_corrector_prompt(original=raw_sql, broken=broken, errors=errors, history=history)
            _t0 = _time.monotonic()
            resp = llm.invoke([HumanMessage(content=prompt)])
            _writer_ms += (_time.monotonic() - _t0) * 1000.0
            _bill(resp)
            repaired = backend.extract_query(resp.content)
    except Exception as _e:
        which = _writer_which(); repaired = None
        writer_error = f"{writer_label(which)} call failed: {str(_e)[:160]}"  # ex.: modelo inexistente / conexão / timeout do coder
        log.warning(f"[corrector] WRITER call FAILED — {writer_error}")
    # No-op repair (failed, empty, unchanged, or REVERTED to the original) → hand back to the normal
    # reflection loop. A revert to the original is NOT a repair: the prompt forbids it, and accepting it
    # surfaces as a no_change "approval" that then burns expensive quality-retries (observed on q9 — the
    # coder couldn't mechanically rebuild missing CTEs, so it reverted, and the slow original re-executed).
    # The corrector REPAIRS the strategy or does nothing — it never destroys it by reverting.
    log.info(f"[corrector] raw repair ({attempts}, writer={which}): {(repaired or '')[:220]!r}")
    _be = get_backend()
    _gen = two_agent_mode() and bool(state.get("strategy"))   # modo GERAR (2-agentes) vs CORRIGIR (B1)
    base = {**state, "corrector_attempts": attempts, "attempt_history": hist,
            "writer_inference_ms": round(_writer_ms, 1), "writer_tokens_in": _tok_in,
            "writer_tokens_out": _tok_out, "writer_calls": _calls}
    # No-op / revert ao ORIGINAL → descarta. (No modo CORRIGIR, reverter ao botch também é no-op.)
    _revert = (_norm(repaired) == _norm(raw_sql)) if _gen else (_norm(repaired) in (_norm(broken), _norm(raw_sql)))
    if not repaired or _revert:
        log.info("[corrector] DISCARDED — empty / reverteu ao original" + ("" if _gen else " ou ao botch"))
        return {**base, "from_corrector": False, **({"writer_error": writer_error} if writer_error else {})}
    if _gen:
        # GERAR: não há botch a preservar — só barra reverter ao original (inclusive ≈original, não-exato)
        import difflib
        if difflib.SequenceMatcher(None, _norm(repaired), _norm(raw_sql)).ratio() > 0.95:
            log.info("[corrector] DISCARDED — ≈ original (não aplicou a estratégia)")
            return {**base, "from_corrector": False}
    else:
        # CORRIGIR: prova que só REPAROU (não mudou a estratégia do botch) — gate FORTE (Jaccard + AST + revert)
        _tp_ok, _tp_why = _be.technique_preserved(broken, repaired, original=raw_sql)
        if not _be.corrector_preserved_strategy(raw_sql, broken, repaired) or _tp_ok is False:
            log.info(f"[corrector] DISCARDED — changed strategy ({_tp_why if _tp_ok is False else 'token-set overlap below threshold'})")
            return {**base, "from_corrector": False}
    # ADERÊNCIA (opt-in WRITER_ADHERENCE_CHECK): juiz LLM LOCAL independente do writer → o SQL aplicou a
    # TÉCNICA da estratégia do architect, ou re-otimizou sozinho? MÉTRICA gravada (não gate — falso-positivo
    # do juiz não deve barrar land válido). Protege a atribuição FIND-qwen / WRITE-cloud. reasoning=False
    # (é classificação, como o labeler). Fail-open: qualquer erro → adherence=None.
    adherence = None
    if _gen and os.getenv("WRITER_ADHERENCE_CHECK", "").strip().lower() in ("1", "true", "on", "yes"):
        try:
            from src.connections import get_llm
            from langchain_core.messages import HumanMessage as _HM
            _jprompt = _be.build_adherence_prompt(state["strategy"], raw_sql, repaired)
            _jresp = get_llm(reasoning=False).invoke([_HM(content=_jprompt)]).content
            _first = next((ln.strip() for ln in (_jresp or "").splitlines() if ln.strip()), "")
            _verdict = "adherent" if _first.upper().startswith("ADHERENT") else ("deviated" if "DEVIAT" in _first.upper() else "unclear")
            adherence = {"verdict": _verdict, "judge_note": (_jresp or "")[:200]}
            log.info(f"[corrector] ADHERENCE judge → {_verdict}")
        except Exception as _e:
            log.info(f"[corrector] ADHERENCE check failed (fail-open): {_e}")
    # ADHERENCE GATE (opt-in ADHERENCE_GATE — decisão 2026-08-02): a estratégia DEVE ser preservada.
    # DEVIATED → DESCARTA e re-alimenta o loop com a causa (o coder refaz implementando A estratégia).
    # DEFAULT OFF nas células de MEDIÇÃO (comparabilidade: as células medidas rodaram métrica-only;
    # varredura 2026-08-02: 18/1265 lands deviated, ZERO nas células das claims TPC-DS → o gate não
    # altera nenhum resultado reportado). ON = política de PRODUÇÃO (pureza de atribuição; custo medido:
    # descartaria os 18 lands válidos-mas-coder-atribuídos, quase todos IMDb marginais).
    if (adherence and adherence.get("verdict") == "deviated"
            and os.getenv("ADHERENCE_GATE", "").strip().lower() in ("1", "true", "on", "yes")):
        log.info("[corrector] ADHERENCE GATE — deviated rewrite DISCARDED (strategy must be preserved); retry with cause")
        return {**base, "from_corrector": False, "adherence": adherence, "adherence_rejected": True,
                "reflection_errors": ["your previous rewrite DEVIATED from the given strategy — you MUST "
                                      "implement THE strategy's technique; do not substitute a different optimization"]}
    log.info(f"[corrector] ACCEPTED ({'generate' if _gen else 'repair'}) — handing back to executor")
    # RE-VALIDA no executor (limpa errors). from_corrector=True = o WRITER produziu este SQL (b→a). writer_used = quem.
    return {**base, "optimized_sql": repaired, "reflection_errors": [], "from_corrector": True, "writer_used": writer_label(which), "adherence": adherence}
