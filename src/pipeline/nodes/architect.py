import os
import time
from langchain_core.messages import HumanMessage, SystemMessage
from src.connections import get_llm
from src.pipeline.backends import get_backend
from src.pipeline.cache.semantic_cache import SemanticCache
from src.pipeline.state import PipelineState

# Error-based reflection cap. N reflections = N+1 total attempts (T1 + N retries on rejection).
# Default 2 (= 3 attempts). Configurable so the count can be A/B-tested without code edits — MUST
# stay uniform across all models in a comparison (it is a system parameter, not a per-model tune).
MAX_REFLECTION_ITERATIONS = int(os.getenv("REFLECTION_ITERATIONS", "2"))


def _hw_pin() -> dict:
    """HW_PIN: fix the hardware hints to DETERMINISTIC values for the HW_HINTS-as-lever ablation, instead
    of reading the LIVE (volatile) values from the DB. The live `cache_hit_ratio_pct` drifts every run
    (cumulative pg_stat_database) → the prompt was non-reproducible. Pinning holds the HW block constant so
    the FIND is a pure function of the HW SIGNAL, letting us test whether the model reads it SEMANTICALLY
    (expensive I/O → decorrelate) or just gets perturbed. Format (any subset, comma-separated):
      HW_PIN=cache=20,rpc=8.0,spc=1.0,wm=4   → cache_hit_ratio_pct/random_page_cost/seq_page_cost/work_mem_mb
    Empty/unset → {} (caller falls back to live hardware_hints()). Same dict shape build_fallback_prompt reads."""
    raw = os.getenv("HW_PIN", "").strip()
    if not raw:
        return {}
    keymap = {"cache": "cache_hit_ratio_pct", "rpc": "random_page_cost",
              "spc": "seq_page_cost", "wm": "work_mem_mb"}
    pin: dict = {}
    for tok in raw.split(","):
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        k = keymap.get(k.strip().lower())
        if k:
            try:
                pin[k] = float(v.strip())
            except ValueError:
                pass
    return pin


def _invoke_with_deadline(llm, messages):
    """Invoke the LLM, capping a SINGLE call's wall-clock at ARCHITECT_TIMEOUT_S (0 = no cap).
    On the cap, stop streaming and return whatever was generated so far + timed_out=True — so a runaway
    thought is CAPTURED (we see WHERE it got stuck) instead of hanging until the HTTP client gives up.
    This bounds ONE thought, not the whole run (reflection still gets its own attempts).

    Also captures the model's THINKING (chain-of-thought) when reasoning is ON: Ollama returns it in a
    SEPARATE field (`additional_kwargs['reasoning_content']`), NOT inline in content — so without grabbing
    it here the reasoning trace was being LOST (it never reached the suggestion store). Returns
    (content, thinking, timed_out); thinking is "" when reasoning is off."""
    deadline_s = float(os.getenv("ARCHITECT_TIMEOUT_S", "0") or 0)
    if deadline_s <= 0:
        r = llm.invoke(messages)
        return (r.content or ""), ((r.additional_kwargs or {}).get("reasoning_content") or ""), False
    echo = os.getenv("ARCHITECT_ECHO", "").strip().lower() in ("1", "true", "on", "yes")
    t0 = time.perf_counter()
    parts, think_parts, timed_out = [], [], False
    for chunk in llm.stream(messages):
        piece = getattr(chunk, "content", "") or ""
        parts.append(piece)
        tk = (getattr(chunk, "additional_kwargs", {}) or {}).get("reasoning_content") or ""
        if tk:
            think_parts.append(tk)
        if echo:                              # live output to the SERVER terminal (ARCHITECT_ECHO=on)
            print(piece, end="", flush=True)
        if time.perf_counter() - t0 > deadline_s:
            timed_out = True
            break
    if echo:
        print(flush=True)
    return "".join(parts), "".join(think_parts), timed_out


def _reasoning_flag():
    """EXPLICIT thinking control for the architect — the reasoning axis is a CONTROLLED variable, not the
    engine default. `REASONING=on` → True (CoT on, captured), `off` → False (CoT off, unambiguous), unset
    → None (model/engine default — the OLD behaviour, where Ollama defaults think:false, so the reasoning
    models were NOT actually reasoning). For a defensible reasoning-vs-non-reasoning claim, set it per run:
    ON for reasoning models, OFF for non-reasoning. Same-model ON-vs-OFF (e.g. qwen3) = the cleanest test."""
    v = os.getenv("REASONING", "").strip().lower()
    if v in ("1", "true", "on", "yes"):
        return True
    if v in ("0", "false", "off", "no"):
        return False
    return None


def _discovery_mode() -> bool:
    """Discovery-phase switch. When on, every query takes the free-form (generative) path so the
    LLM proposes transforms by WRITING SQL — the substrate the Direction B->A loop learns from.
    Agnostic: "free-form" is the generative path of whatever backend is active; the decomposed
    (structured) path is the consolidated output of discovery, so it is bypassed while bootstrapping.
    """
    return os.getenv("DISCOVERY_MODE", "").strip().lower() in ("1", "true", "on", "yes")


def _find_backend() -> str:
    """Backend do FIND (architect): 'local' (produto — qwen via ollama) | 'cloud' (TETO de FIND — deepseek
    via API). Espelho do WRITER_BACKEND (que escolhe o WRITE). O modo cloud é o **CEILING**: mede quanto do
    teto do modelo leve é CAPACIDADE do FIND × sem-oportunidade — roda o FIND no melhor modelo possível,
    mesmo WRITE, e compara o delta. Quebra o "leve/local" → é medição de teto, NUNCA produção."""
    return os.getenv("FIND_BACKEND", "local").strip().lower()


def _find_llm(reasoning: bool | None = None):
    """O LLM do FIND. Local = get_llm (qwen; reasoning controlável pelo eixo). Cloud = deepseek-reasoner
    (R1, CoT NATIVO — o teto de FIND que raciocina), reusando o cliente ChatOpenAI/deepseek do coder. No
    cloud o `reasoning` é inerente ao modelo (não se passa o toggle do ollama). Modelo configurável via
    CLOUD_FIND_MODEL (default deepseek-reasoner)."""
    if _find_backend() == "cloud":
        from langchain_openai import ChatOpenAI
        model = os.getenv("CLOUD_FIND_MODEL") or "deepseek-reasoner"
        base = os.getenv("CLOUD_FIND_API_BASE") or os.getenv("CLOUD_CODER_API_BASE") or os.getenv("FORMALIZER_API_BASE")
        return ChatOpenAI(
            model=model, base_url=base,
            api_key=os.getenv("KEY_DEEPSEEK") or os.getenv("FORMALIZER_API_KEY") or "", temperature=0,
        )
    return get_llm(reasoning=reasoning)


def _schema_block(backend, raw_sql: str, flag: str) -> str:
    """B (schema-linking no architect): o `relevant_schema_block` (colunas reais + join-keys) SE `flag` estiver
    on; senão "". Gated separado pra medir B-write × B-find isolados. FAIL-OPEN (erro → "", nunca quebra)."""
    if os.getenv(flag, "off").strip().lower() not in ("1", "true", "on", "yes"):
        return ""
    try:
        return backend.relevant_schema_block(raw_sql) or ""
    except Exception:
        return ""


def _true_failure_feedback() -> bool:
    """Lever de minimal-reasoning-efficiency (`TRUE_FAILURE_FEEDBACK`, label `+truefb`): na re-estratégia,
    dizer ao architect a CLASSE REAL do fracasso anterior em vez de colapsar tudo em "could not be written"
    (bug #18). Ajuda o modelo LEVE a usar melhor as tentativas que tem — sem lhe ensinar técnica alguma.
    OFF por padrão: liga-lo incondicionalmente tornaria toda run futura incomparável com a matriz já rodada."""
    return os.getenv("TRUE_FAILURE_FEEDBACK", "off").strip().lower() in ("1", "true", "on", "yes")


def _try_decomposed(raw_sql: str, backend) -> tuple[str | None, list[str], dict]:
    """Decomposed path — DETECTOR-ONLY, NO LLM. This node parses and hands the backend the components;
    the backend's `optimize_decomposed` runs its deterministic transform registry. The node knows no
    transform (no decorrelation / consolidation branch here). Returns (sql|None, suggestions,
    provenance). sql is None when no mapped transform applied -> in production the query is left
    unchanged (the LLM/free-form path is reserved for DISCOVERY)."""
    import logging
    log = logging.getLogger(__name__)

    components = backend.parse_query(raw_sql)
    if components is None:
        return None, [], {"transform": None, "trigger": "none", "suggestions": []}

    sql, prov = backend.optimize_decomposed(raw_sql, components)
    log.info(f"[decomposed] provenance: {prov}")
    return sql, prov.get("suggestions", []), prov


def architect(state: PipelineState) -> PipelineState:
    node_start = time.perf_counter()

    raw_sql = state["raw_sql"]
    reflection_errors: list[str] = state.get("reflection_errors", [])
    iterations: int = state.get("reflection_iterations", 0)
    retry_hint: str | None = state.get("retry_hint")
    retry_attempts: int = state.get("retry_attempts", 0)
    system_prompt: str = state.get("system_prompt", "You are a query optimization expert.")

    # Accumulate every non-empty rewrite attempt for the Direction B->A discovery feed. On a re-entry
    # (reflection on error, or quality retry) the PREVIOUS optimized_sql in state is the attempt that
    # was just rejected/weak — capture it before this call overwrites it. This makes attempts that
    # failed with a DB EXECUTION error (UndefinedColumn/UndefinedTable) labelable too: rejected_sql_attempts
    # only tracks STRUCTURAL rejects (for loop detection), so execution-error attempts that nonetheless
    # reached the right technique were invisible and UNDER-COUNTED cross-model convergence (the thesis signal).
    attempt_history: list[str] = list(state.get("attempt_history") or [])
    prev_attempt = (state.get("optimized_sql") or "").strip()
    if (reflection_errors or retry_hint) and prev_attempt and prev_attempt not in attempt_history:
        attempt_history.append(prev_attempt)

    # 1. Semantic cache — skip on reflection (needs rework) and on quality retry (previous result was weak).
    # DISABLE_SEMANTIC_CACHE=on forces every run COLD (the matrix needs this — otherwise runs 2/3 of the
    # same query hit the cache and skip the model, faking a 0-inference "instant" result).
    _cache_off = os.getenv("DISABLE_SEMANTIC_CACHE", "").strip().lower() in ("1", "true", "on", "yes")
    if not reflection_errors and not retry_hint and not _cache_off:
        cache = SemanticCache()
        cached = cache.lookup(raw_sql)
        if cached:
            return {**state, "optimized_sql": cached, "cache_hit": True, "status": "cache_hit"}

    # 2. Plan-Aware Interception — delegate to backend (agnostic)
    backend = get_backend()
    explain = backend.explain(raw_sql)
    # Eixo de ablação PLAN_HINTS: off = NÃO passa o PLANO de execução ao architect (mede se o advisor é de
    # fato "plan-driven" ou otimiza IGUAL só pela ESTRUTURA da query). Mantém o SCHEMA (vem do schema_analyst,
    # é outro nó) → nível (b) "sem-plano-com-schema". Retido → os builders mostram "plan unavailable" e as
    # table_estimates ficam vazias. Default = on (comportamento atual). Label +noplan.
    if os.getenv("PLAN_HINTS", "on").strip().lower() in ("0", "false", "off", "no"):
        # ⚠️ 29/08: a mensagem NÃO vai mais para o prompt — os builders passaram a emitir bloco VAZIO
        # quando `explain` traz `error`. A string abaixo é só o MARCADOR interno de "plano retido".
        # Motivo: o texto antigo ia literalmente ao modelo e (a) prometia um schema que, com
        # SCHEMA_LINKING=off, não estava lá; (b) vazava vocabulário do experimento ("ablation").
        explain = {"error": "plan withheld (PLAN_HINTS=off)"}
    # Eixo de ablação HW_HINTS: off = NÃO injeta hardware no prompt (mede se o hardware é lever de fato,
    # já que somos REWRITER — o banco re-otimiza e o plano recebido já embute a config física). Vazio → os
    # builders pulam o bloco (`if hw_hints:`). Default = on (mantém comportamento atual).
    if os.getenv("HW_HINTS", "on").strip().lower() in ("0", "false", "off", "no"):
        hw_hints = {}
    else:
        hw_hints = state.get("hw_override") or _hw_pin() or backend.hardware_hints()

    llm = _find_llm(reasoning=_reasoning_flag())   # FIND_BACKEND: local (qwen, reasoning-controlável) | cloud (CEILING = deepseek-reasoner)
    schema_context: dict = state.get("schema_context", {})

    # 3a. (HISTÓRICO) Decomposed/detector path — REMOVIDO da live path no pivô 2026-06-19.
    # O architect roda free-form em TODO run (o `optimized_sql=None` abaixo é hardcoded). DISCOVERY_MODE
    # NÃO troca mais o caminho do architect — só gateia a escalada pro índice em _route_after_executor
    # (graph.py). O detector (optimize_decomposed/_reconstruct_*) segue no backend como BASELINE de
    # "WRITE determinístico" pro doutorado, mas NÃO é chamado aqui (não confundir com _try_decomposed,
    # que existe mas está morto na live path).
    table_estimates = backend.extract_table_estimates(explain)
    # ADVISOR (pivot 2026-06-19 — MEASUREMENT paper, see novas_direcoes_paper.md §6): the LLM ALWAYS proposes
    # (free-form), reading plan + hardware + metrics; the S=1 gate validates and the DBA reviews. The
    # deterministic detector LEFT the production path — it was STATIC (shape matches → fixed rewrite) and
    # contradicted the dynamic question we measure ("the model WEIGHS the context and decides"). The
    # detector code (optimize_decomposed/_reconstruct_decorrelation/_build_consolidation) remains in the backend
    # as a "deterministic WRITE" BASELINE for study (doctorate), but is no longer called here.
    # APPLY_HEURISTICS still distinguishes unaided (raw capability) vs guided (injected hint-B).
    optimized_sql, new_suggestions = None, []
    strategy_out = state.get("strategy")   # 2-agentes: a estratégia do architect (None no modo raw)
    decomposed_prov = {"transform": None, "trigger": "free_form", "suggestions": []}

    # Accumulate suggestions across all attempts (deduplicated, order-preserving)
    existing_suggestions: list[str] = state.get("suggestions", [])
    for s in new_suggestions:
        if s not in existing_suggestions:
            existing_suggestions.append(s)

    # 3b. Fallback: free-form prompt if decomposed path failed or not applicable
    used_fallback = False
    architect_timed_out = False
    cached_strategy_id = None   # #12: set when the bypass reuses a cached strategy (reasoning skipped)
    rules_in_prompt = state.get("rules_in_prompt", [])
    reasoning_trace = state.get("reasoning_trace", "")
    if optimized_sql is None:
        used_fallback = True
        # PROVENANCE: record which learned rules were actually injected into THIS prompt (arch B).
        # Only the fallback path injects learned_section, so attribution is recorded only here.
        from src.pipeline.discovery.learned_rules import active_rule_names
        rules_in_prompt = active_rule_names()
        # Pass the prior attempts (the "experience" — inspired by LLM4IA): the prompt lists them so the
        # model does not loop, with the rewrite-specific nuance (retry a strategy that only botched the SQL;
        # abandon one that was valid-but-not-faster). Only on reflection/retry is attempt_history non-empty.
        from src.pipeline.nodes.corrector import two_agent_mode
        if two_agent_mode():
            # 2-AGENTES: o architect DECIDE a estratégia (NL-estruturada, SEM SQL); o writer escreve dela.
            # #12 BYPASS: se existe uma estratégia VALIDADA para ESTA forma (match estrutural), reusa-a e
            # PULA o reasoning (caminho de eficiência = reasoning-zero). Só no 1º passe — em re-estratégia
            # (reflection/retry) o cacheado já falhou ou precisamos de algo DIFERENTE, então raciocina do zero.
            # O writer ainda ESCREVE e o executor RE-VALIDA (S=1 + ganho); o hook do input_router reforça/
            # rebaixa a entrada por cached_strategy_id. Inerte quando STRATEGY_CACHE=off.
            from src.pipeline.discovery import strategy_cache
            _cache_hit = (strategy_cache.lookup(raw_sql, dialect=backend.sqlglot_dialect())
                          if (strategy_cache.enabled() and not reflection_errors and not retry_hint)
                          else None)
            if _cache_hit is not None:
                strategy_out = _cache_hit["strategy"]
                optimized_sql = None                                                 # o WRITER produz o SQL
                architect_timed_out = False
                cached_strategy_id = _cache_hit["id"]
                reasoning_trace = f"[cached strategy {_cache_hit['id']} — {_cache_hit['technique']}; reasoning bypassed (#12)]"
                rules_in_prompt = [f"cache:{_cache_hit['id']}"]                       # PROVENANCE do hit
                decomposed_prov = {**decomposed_prov, "trigger": "cached_strategy", "transform": None}
            else:
                # Em re-estratégia, passa a anterior + por que falhou (inescrevível vs no-gain) → propõe DIFERENTE.
                # BUG #18 (2026-07-19): `reflection_errors` colapsava TUDO em "could not be written" — então
                # quando a ESTRATÉGIA do architect quebrava a equivalência (S≠1, culpa DELE), ele era informado
                # de que o CODER não conseguiu escrever. Atribuição invertida: ele concluía "o escritor é ruim"
                # e trocava de técnica, em vez de saber que a transformação escolhida não preserva o resultado.
                # O sinal correto SEMPRE existiu (o executor grava "Integrity check failed … (S≠1)" em
                # reflection_errors) — só era achatado no caminho de volta.
                # Atrás de toggle: ligar incondicionalmente tornaria TODA run futura incomparável com a matriz
                # já rodada (PG inteiro + MySQL M1/M2/M3/M3-A foram produzidos com o comportamento antigo).
                # ⚠️ NÃO é seeding: informa o FATO que o S=1 apurou ("retornou linhas diferentes"), nunca a
                # correção ("use X em vez de Y") — a regra continua sendo o architect que tem que inferir.
                _prev_why = ("could not be written" if reflection_errors
                             else ("no gain — try a different technique" if retry_hint else None))
                if reflection_errors and _true_failure_feedback():
                    _blob = " ".join(str(e) for e in reflection_errors).lower()
                    if any(k in _blob for k in ("integrity check failed", "hashes differ", "s≠1",
                                                "different number of rows")):
                        _prev_why = ("your previous strategy WAS written and DID run, but the result set it "
                                     "returned is NOT the same as the original's — the transformation you "
                                     "chose does not preserve equivalence on this data. Propose a strategy "
                                     "whose result set is provably identical.")
                # B-find: schema no prompt de DECISÃO (SCHEMA_LINKING_FIND) → o FIND mira melhor coluna/chave.
                sprompt = backend.build_strategy_decision_prompt(
                    raw_sql, explain, hw_hints, state.get("strategy"), _prev_why,
                    schema_context=_schema_block(backend, raw_sql, "SCHEMA_LINKING_FIND"))
                content, thinking, architect_timed_out = _invoke_with_deadline(llm, [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=sprompt),
                ])
                reasoning_trace = (f"<think>\n{thinking}\n</think>\n\n" if thinking else "") + content
                strategy_out = None if architect_timed_out else (content or "").strip()  # a estratégia É o conteúdo (sem extract_query)
                optimized_sql = None                                                     # o WRITER produz o SQL
                decomposed_prov = {**decomposed_prov, "trigger": "two_agent_strategy", "transform": None}
        else:
            # B-write: schema no prompt do RAW architect (SCHEMA_LINKING) → não alucina coluna/alias ao escrever.
            prompt = backend.build_fallback_prompt(
                raw_sql, explain, reflection_errors, hw_hints, retry_hint, attempt_history,
                schema_context=_schema_block(backend, raw_sql, "SCHEMA_LINKING"))
            content, thinking, architect_timed_out = _invoke_with_deadline(llm, [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt),
            ])
            # reasoning_trace = the model's THINKING (CoT, when reasoning is ON) wrapped in <think>, + the output.
            reasoning_trace = (f"<think>\n{thinking}\n</think>\n\n" if thinking else "") + content
            # On a deadline hit (runaway <think>) the output is truncated → no usable SQL; keep the partial reasoning.
            optimized_sql = None if architect_timed_out else backend.extract_query(content)
            decomposed_prov = {**decomposed_prov, "trigger": "free_form", "transform": None}

    # Cache is stored by the executor node after Phase B approval — not here — to avoid caching invalid rewrites.

    elapsed_ms = round((time.perf_counter() - node_start) * 1000, 1)

    return {
        **state,
        "explain_plan": explain,
        "optimized_sql": optimized_sql,
        "cache_hit": False,
        "retry_hint": None,       # clear; executor will re-set if result is still weak
        "retry_attempts": retry_attempts + (1 if retry_hint else 0),
        "reflection_iterations": iterations + (1 if reflection_errors else 0),
        "inference_ms": round(state.get("inference_ms", 0) + elapsed_ms, 1),
        "suggestions": existing_suggestions,
        "attempt_history": attempt_history,
        "used_fallback": used_fallback,
        "rules_in_prompt": rules_in_prompt,
        "reasoning_trace": reasoning_trace,
        "architect_timed_out": architect_timed_out,  # single-call deadline hit → outcome="timeout" (class-c) in input_router
        "decomposed_provenance": decomposed_prov,   # {transform, trigger, suggestions} — read by Phase 4 provenance
        "from_corrector": False,    # the architect (model) produced this SQL, NOT the corrector — clears any prior flag
        # 2-AGENTES: a estratégia (None no raw) + contadores. Nova estratégia → reseta writer_attempts (corrector_attempts).
        "strategy": strategy_out,
        "strategy_attempts": state.get("strategy_attempts", 0) + (1 if strategy_out is not None and decomposed_prov.get("trigger") in ("two_agent_strategy", "cached_strategy") else 0),
        "corrector_attempts": 0 if decomposed_prov.get("trigger") in ("two_agent_strategy", "cached_strategy") else state.get("corrector_attempts", 0),
        # #12: id da entrada do cache quando o bypass reusou uma estratégia (o hook do input_router reforça/rebaixa por ele).
        "cached_strategy_id": cached_strategy_id,
        # #12: flag STICKY p/ a curva de auto-melhoria — True se QUALQUER passe usou o cache (não reseta na re-estratégia).
        # Separado de cached_strategy_id (por-passe, p/ atribuição do reforço): mede "pulou reasoning ao menos 1×".
        "used_strategy_cache": bool(cached_strategy_id) or state.get("used_strategy_cache", False),
        "status": "optimized",
    }
