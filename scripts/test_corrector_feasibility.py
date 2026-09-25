#!/usr/bin/env python3
"""Passo 1 — VIABILIDADE do reparador (degrau 2), ISOLADO (sem grafo, sem servidor).

Pergunta: o coder LOCAL (qwen2.5-coder:7b) conserta os class-b já no store? Se um repair PRESERVA a
estratégia E passa no S=1, então b→a → **o FIND original era VÁLIDO** (gargalo era WRITE, não FIND).
É a sonda de reparo da RQ5, rodada à mão sobre o que JÁ falhou — sem tocar no pipeline/campanha.

NÃO mexe no grafo. NÃO precisa do servidor. Fala direto com Ollama (coder) + DB (S=1).
Reusa as peças prontas: backend.build_corrector_prompt / extract_query / corrector_preserved_strategy /
sample_hash, e corrector._coder_llm (CORRECTOR_MODEL=qwen2.5-coder:7b, LOCAL).

Run (mesmo .env do servidor — CORRECTOR_MODEL=qwen2.5-coder:7b, DB_URI do TPC-DS PG):
    python scripts/test_corrector_feasibility.py                 # todos os class-b do qwen no PG
    python scripts/test_corrector_feasibility.py --only q38 q9   # só essas
    python scripts/test_corrector_feasibility.py --model qwen-reasoning+think --limit 5
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage
from src.connections import get_llm
from src.pipeline.backends import get_backend, get_db_type
from src.pipeline.nodes.corrector import _coder_llm, _norm, _writer_which, writer_label
from src.pipeline.nodes.executor import _robust_explain_analyze  # tempo real (warm-up+mediana), p/ o ganho

_CLASS_B = ("mechanics_failed", "equivalence_failed_semantic", "equivalence_failed_structural")


def _err_kind(msg: str) -> str:
    """Classifica o erro do engine — só DIALETO (PG-ism) decide especializar o coder por backend.
    Timeout (lento) e escopo (coluna) NÃO são dialeto."""
    m = (msg or "").lower()
    if not m:
        return "sem hash"
    if any(k in m for k in ("3024", "maximum statement", "statement timeout", "canceling statement", "interrupted")):
        return "TIMEOUT (lento, NÃO-dialeto)"
    if any(k in m for k in ("1054", "unknown column", "does not exist", "undefined")):
        return "ESCOPO/coluna (NÃO-dialeto)"
    if any(k in m for k in ("1235", "not supported", "full outer join", "no support")):
        return "⚠️ DIALETO (feature) → PG-ism → especializar coder"
    if any(k in m for k in ("1064", "syntax error", "near '")):
        return "⚠️ DIALETO? (syntax) → pode ser PG-ism"
    if any(k in m for k in ("1146", "table") ) and "exist" in m:
        return "tabela inexistente"
    return "outro"


# Prior-por-técnica p/ adjudicar b_nonequiv (S≠1 persistente): a técnica alcançada é equivalência-GARANTIDA
# (existe equivalente → não-equiv = WRITE-duro) ou CONDICIONAL (só equivale com dedup/count → não-equiv = FIND-inválido provável)?
_EQUIV_GARANTIDA = ("decorrel", "window", "pre-filter", "prefilter", "pré-filtro", "pre filter",
                    "reorder", "reordenar", "comma", "explicit join", "explicit inner", "inner join",
                    "pushdown", "push filter", "push the filter", "materializ")
_CONDICIONAL = ("in subquery", "in→", "in to", "semi-join", "semi join", "intersect", "except",
                "union", "or→", "or to union", "exists", "distinct")


def _technique_prior(suggestions) -> str:
    """Prior pra desambiguar um b_nonequiv. CONDICIONAL tem prioridade (é a fonte do risco de equivalência)."""
    s = " ".join(suggestions or []).lower()
    if not s:
        return "técnica não rotulada → reconstruir à mão"
    if any(k in s for k in _CONDICIONAL):
        return "FIND-inválido PROVÁVEL (técnica CONDICIONAL — precisa dedup/count; confirmar c/ reconstrução)"
    if any(k in s for k in _EQUIV_GARANTIDA):
        return "WRITE-duro PROVÁVEL (técnica equivalência-GARANTIDA → equivalente existe)"
    return "técnica não classificada → reconstruir à mão"


def _qname(raw: str) -> str:
    import hashlib
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    for qf in glob.glob("queries/**/*.json", recursive=True):
        try:
            q = json.load(open(qf))
        except Exception:
            continue
        sql = q.get("sql") or q.get("query") or ""
        if sql and hashlib.sha256(sql.encode()).hexdigest()[:12] == h:
            return qf.split("/")[-1].replace(".json", "")
    return h[:8]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen-reasoning", help="label EXATO (ex.: qwen-reasoning [bare/on-default], qwen-reasoning+think, qwen-reasoning+nothink)")
    ap.add_argument("--engine", default=os.getenv("DB_TYPE", "postgres"))
    ap.add_argument("--only", nargs="*", help="nomes de query (q38 q9 …)")
    ap.add_argument("--limit", type=int, default=0, help="máximo de casos (0 = todos)")
    ap.add_argument("--no-gain", action="store_true", help="só S=1 (pula a medição de tempo — mais rápido)")
    ap.add_argument("--md", nargs="?", const="AUTO", default=None,
                    help="salva resultado em markdown; sem caminho → experiments/{model}/{engine}/repair_probe.md")
    ap.add_argument("--generate", action="store_true",
                    help="degrau 4 (GERAR-da-estratégia): FIND-agent descreve a estratégia em palavras → coder "
                         "ESCREVE do zero (não patcheia o botch). Mede o split FIND/WRITE (mecanismo QUITE).")
    ap.add_argument("--audit", default=None,
                    help="DIAGNÓSTICO: salva os pares (botch, repair) que alcançaram S=1 num JSONL, "
                         "com o veredito ESTRUTURAL (technique_preserved) ao lado do Jaccard — pra medir "
                         "quantos reparos S=1 NÃO preservaram a técnica (auditoria do gate).")
    args = ap.parse_args()

    backend = get_backend()
    env_engine = get_db_type()
    if args.engine != env_engine:  # guard: --engine só FILTRA o store; o DB real vem do .env (DB_URI)
        sys.exit(f"❌ --engine {args.engine} mas o .env aponta DB_TYPE={env_engine}. "
                 f"Os registros {args.engine} seriam verificados contra o DB {env_engine} (errado). "
                 f"Troque o .env pro {args.engine} (DB_URI + VERIFY_DB_URI) antes de rodar.")
    _which = _writer_which()
    coder = _coder_llm(_which)
    print(f"coder = {writer_label(_which)} "
          f"(LOCAL={'sim' if _which == 'local' else '⚠️ NUVEM!'}) | engine={args.engine}\n")

    # 1 botch por query (o mais recente) — evita repetir a mesma query 3×
    seen, cases = set(), []
    for f in sorted(glob.glob(os.path.join(os.getenv("SUGGESTION_STORE_DIR", ".cache/suggestions"), "*.json")), reverse=True):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if (d.get("model", "") or "") != args.model:  # EXATO, não substring: 'qwen-reasoning' não pode casar 'qwen-reasoning+think'
            continue
        if (d.get("db_type") or "") != args.engine:
            continue
        if not (args.generate and d.get("strategy")) and d.get("outcome") not in _CLASS_B:
            continue  # replay (--generate c/ estratégia): cobre TODAS as estratégias do pipeline (inclui a que landou) → /9
        broken = (d.get("reached_sql") or d.get("optimized_sql") or "").strip()
        raw = (d.get("raw_sql") or "").strip()
        if not broken or not raw:
            continue
        qn = _qname(raw)
        if args.only and qn not in args.only:
            continue
        key = (qn, _norm(broken))  # 1 por BOTCH distinto: mesmo broken nas 3 runs colapsa; broken diferente = 2 unidades
        if key in seen:
            continue
        seen.add(key)
        cases.append((qn, raw, broken, d.get("errors") or [], d.get("outcome"), d.get("suggestions") or [], d.get("strategy")))
    if args.limit:
        cases = cases[:args.limit]

    nq = len(set(c[0] for c in cases))
    print(f"botches distintos a testar: {len(cases)} (de {nq} queries) — {[c[0] for c in cases]}\n")
    from collections import Counter
    buckets = Counter()  # 'a' / 'd' / 'reg' / 'b' / 'nogain'
    rows = []            # (query, outcome, classe, detalhe) — pro --md
    audit = []           # pares (botch, repair) que alcançaram S=1 + veredito estrutural — pro --audit
    budget = int(os.getenv("EXPLAIN_ANALYZE_TIMEOUT_S", "120"))
    vb = int(os.getenv("VERIFY_SAMPLE_HASH_TIMEOUT_S", "60"))
    max_iters = int(os.getenv("CORRECTOR_REFLECT_ITERS", "2"))  # LOOP de reflexão do coder
    rich_fb = os.getenv("RICH_EQUIVALENCE_FEEDBACK") == "1"  # degrau 2: no S≠1, diz QUAIS linhas divergem (priv-safe)
    cot_coder = os.getenv("COT_CODER") == "1"  # degrau 3: MESMO coder raciocina (CoT) antes de escrever
    if rich_fb:
        print("→ momento 2 ON: RICH_EQUIVALENCE_FEEDBACK (diff de contagem/forma no S≠1)")
    if cot_coder:
        print("→ momento 3 ON: COT_CODER (coder raciocina em palavras antes de escrever o SQL)")
    if rich_fb or cot_coder:
        print()

    find_llm = None
    if args.generate:  # FIND-agent é LAZY — replay c/ estratégia PERSISTIDA não precisa do FIND model (só do writer)
        print("→ degrau 4 ON: GERAR-da-estratégia (estratégia persistida → replay; senão deriva da reescrita)\n")

    def _strip_think(t: str) -> str:  # estratégia do FIND-agent: tira o CoT, fica só a descrição
        import re as _re
        return _re.sub(r"<think>.*?</think>", " ", t or "", flags=_re.DOTALL | _re.IGNORECASE).strip()

    schema_ctx_map = {}
    if os.getenv("SCHEMA_LINKING") == "1" and not args.generate:  # CORRIGIR: schema por embeddings (legado). GERAR usa o determinístico (relevant_schema_block), igual ao pipeline ao vivo
        import numpy as _np, faiss as _faiss
        from langchain_ollama import OllamaEmbeddings
        cat = backend.schema_catalog()                                    # {tabela: [(col,tipo)]}
        col_docs = [(t, f"{t}.{c} {ty}") for t, cols in cat.items() for c, ty in cols]
        emb = OllamaEmbeddings(model=os.getenv("EMBED_MODEL", "nomic-embed-text"))
        mat = _np.array(emb.embed_documents([d[1] for d in col_docs]), dtype="float32")
        _faiss.normalize_L2(mat); _idx = _faiss.IndexFlatIP(mat.shape[1]); _idx.add(mat)
        topk = int(os.getenv("SCHEMA_LINK_TOPK", "40"))
        def _schema_block(raw_sql):
            qv = _np.array([emb.embed_query(raw_sql)], dtype="float32"); _faiss.normalize_L2(qv)
            _, ii = _idx.search(qv, min(topk, len(col_docs)))
            tables = {col_docs[i][0] for i in ii[0]} | set(backend._base_tables(raw_sql) or [])  # floor: tabelas da query
            lines = [f"- {t}(" + ", ".join(c for c, _ in cat[t]) + ")" for t in sorted(tables) if t in cat]
            return "AVAILABLE COLUMNS (these exist — use ONLY these, never invent a column):\n" + "\n".join(lines) + "\n\n"
        for _raw in {c[1] for c in cases}:
            schema_ctx_map[_raw] = _schema_block(_raw)
        print(f"→ schema-linking ON: {len(col_docs)} colunas indexadas (nomic-embed-text+faiss); blocos p/ {len(schema_ctx_map)} queries\n")

    ref_cache = {}       # raw → (hash, uri, onde, orig_timeout, motivo): 1 referência QUENTE por query
    ref_rows_cache = {}  # (raw, uri) → linhas-amostra do original (degrau 2), reusadas nos botches

    def _reference(raw):
        """Gabarito de equivalência do original — calculado 1× por query (quente) e reusado nos botches.
        full primeiro; se estourar, SF1 com WARM-UP (1ª passada aquece o cache, 2ª pega o hash quente)."""
        if raw in ref_cache:
            return ref_cache[raw]
        oh, oerr = backend.sample_hash(raw)
        timed_out = bool(oerr) and backend.is_timeout_error(oerr)
        res = (oh, None, "full", timed_out, _err_kind(oerr))
        if not oh and oerr and os.getenv("VERIFY_DB_URI"):
            vuri, oh2, oerr2 = os.getenv("VERIFY_DB_URI"), None, None
            for attempt in range(2):  # 1ª passada aquece o cache (pode estourar), 2ª pega o hash quente
                oh2, oerr2 = backend.sample_hash(raw, timeout_s=vb, uri=vuri)
                if oh2:
                    break
                if attempt == 0:
                    print(f"   [aquecendo SF1… 1ª passada estourou: {_err_kind(oerr2)}]")
            if oh2:
                res = (oh2, vuri, "SF1", timed_out, _err_kind(oerr))
            else:
                print(f"   [orig INVERIFICÁVEL] full[{_err_kind(oerr)}] SF1[{_err_kind(oerr2)}]")
                res = (None, None, "full", timed_out, _err_kind(oerr))
        ref_cache[raw] = res
        return res

    for qn, raw, broken, errors, outcome, suggs, persisted_strategy in cases:
        print(f"── {qn} ({outcome}) ──")
        cls, detail = "b_revert", ""
        # Referência de equivalência do ORIGINAL — 1× por query (quente), reusada nos botches
        ref_hash, ref_uri, verified_on, orig_timed_out, ref_why = _reference(raw)
        if not ref_hash:
            cls, detail = "b_unverif", f"original inverificável ({ref_why})"
            print("   → b→b (original inverificável — nem dá pra checar equivalência)")
        else:
            # LOOP de reflexão: coder repara → re-valida (roda? equivalente?) → devolve o erro → retenta (≤max_iters)
            broken_now, errs_now, history, repaired, s1 = broken, errors, [], None, False
            strategy = None
            if args.generate:
                if persisted_strategy:  # REPLAY: usa a ESTRATÉGIA gravada do live 2-agentes (mesmo FIND, troca o writer)
                    strategy = persisted_strategy
                    print(f"   [estratégia PERSISTIDA] {strategy[:100]}…")
                else:  # sem persistida → deriva da reescrita (carrega o FIND-agent LAZY na 1ª vez)
                    try:
                        if find_llm is None:
                            _fam = args.model.split("+")[0]
                            _rsn = True if "+think" in args.model else (False if "+nothink" in args.model else None)
                            find_llm = get_llm(family=_fam, reasoning=_rsn)
                        strategy = _strip_think(find_llm.invoke([HumanMessage(content=backend.build_strategy_prompt(raw, broken))]).content)
                        print(f"   [estratégia derivada] {strategy[:100]}…")
                    except Exception as e:
                        strategy = ""; print(f"   [estratégia] falhou: {e}")
            for k in range(1, max_iters + 1):
                try:
                    if args.generate:  # coder ESCREVE do zero a partir da estratégia (não vê o botch)
                        _gschema = backend.relevant_schema_block(raw) if os.getenv("SCHEMA_LINKING", "").strip().lower() in ("1", "true", "on", "yes") else ""
                        prompt = backend.build_generate_prompt(original=raw, strategy=strategy, errors=errs_now,
                                                               history="\n\n".join(history[-2:]), cot=cot_coder, schema_context=_gschema)
                    else:
                        prompt = backend.build_corrector_prompt(original=raw, broken=broken_now, errors=errs_now,
                                                                history="\n\n".join(history[-2:]), cot=cot_coder,
                                                                schema_context=schema_ctx_map.get(raw, ""))
                    cand = backend.extract_query(coder.invoke([HumanMessage(content=prompt)]).content)
                except Exception as e:
                    cls, detail = "b_revert", f"coder erro: {e}"; break
                if not cand or _norm(cand) in (_norm(broken), _norm(raw)) or (history and _norm(cand) == _norm(history[-1])):
                    cls, detail = "b_revert", f"no-op/revert (iter {k})"
                    if k == 1: print("   ❌ no-op/revert (coder não soube reparar)")
                    break
                if args.generate:  # gerar-da-estratégia: não há botch a "preservar"; só barra reverter ao original
                    import difflib as _dl
                    if _dl.SequenceMatcher(None, _norm(cand), _norm(raw)).ratio() > 0.95:
                        errs_now = ["Você praticamente REPRODUZIU o original — APLIQUE a estratégia, não devolva o original."]
                        history.append(cand); broken_now = cand; cls, detail = "b_revert", f"≈original (iter {k})"; continue
                    if os.getenv("WRITER_LINT", "").strip().lower() in ("1", "true", "on", "yes"):  # paridade c/ pipeline: lint pré-execução
                        _lok, _lmsg = backend.lint_self_consistency(cand)
                        if not _lok:
                            print(f"   [iter {k}] lint reject: {_lmsg[:70]}")
                            errs_now = [f"SELF-CONSISTENCY error in your previous rewrite — you MUST fix it: {_lmsg}"]
                            history.append(cand); broken_now = cand; cls, detail = "b_lint", f"lint reject (iter {k})"; continue
                else:
                    tp_ok, tp_why = backend.technique_preserved(broken, cand, original=raw)  # AST: mesma técnica + não reverteu
                    if not backend.corrector_preserved_strategy(raw, broken, cand) or tp_ok is False:
                        why = tp_why if tp_ok is False else "mudou estratégia (Jaccard baixo)"
                        if os.getenv("DUMP_REJECTED"):  # inspeção: candidato que o gate barrou (re-opt vs false-reject)
                            with open(os.getenv("DUMP_REJECTED"), "a") as _fh:
                                _fh.write(json.dumps({"query": qn, "iter": k, "why": why, "broken": broken, "cand": cand, "original": raw}, ensure_ascii=False) + "\n")
                        errs_now = ["Você MUDOU a estratégia ou REVERTEU ao original — mantenha a TÉCNICA do rewrite quebrado, "
                                    "conserte SÓ a mecânica do SQL. NÃO volte para a query original nem troque de transformação."]
                        history.append(cand); broken_now = cand; cls, detail = "b_revert", f"{why} (iter {k})"; continue
                rh, rerr = backend.sample_hash(cand, timeout_s=(vb if ref_uri else None), uri=ref_uri)
                if rerr:  # não rodou → devolve o erro do engine e retenta
                    k_kind = _err_kind(rerr)
                    print(f"   [iter {k}] reparado falhou [{k_kind}]: {rerr[:60]}")
                    errs_now = [rerr]; history.append(cand); broken_now = cand
                    cls, detail = ("b_dialeto" if "DIALETO" in k_kind else "b_exec"), f"{k_kind} (iter {k})"; continue
                if rh == ref_hash:
                    repaired, s1 = cand, True; print(f"   ✓ equivalente (iter {k}, via {verified_on})"); break
                # rodou mas S≠1 → devolve e retenta
                print(f"   [iter {k}] S≠1 (linhas diferentes)")
                rich_msg = None
                if rich_fb:  # degrau 2: diz QUAIS/QUANTAS linhas divergem (priv-safe: contagem/forma, nunca valor)
                    rk = (raw, ref_uri)
                    if rk not in ref_rows_cache:
                        ref_rows_cache[rk], _ = backend.sample_rows(raw, n=100, timeout_s=(vb if ref_uri else None), uri=ref_uri)
                    cand_rows, _ = backend.sample_rows(cand, n=100, timeout_s=(vb if ref_uri else None), uri=ref_uri)
                    rich_msg = backend.equivalence_diff(ref_rows_cache[rk], cand_rows)
                    if rich_msg: print(f"      ↳ rich-fb: {rich_msg[:90]}…")
                errs_now = [rich_msg] if rich_msg else \
                    ["Sua reescrita retorna LINHAS DIFERENTES do original (S≠1). Preserve EXATAMENTE o mesmo result-set."]
                history.append(cand); broken_now = cand; repaired = cand; cls, detail = "b_nonequiv", f"S≠1 persistente ({k} tent.)"
            if s1:  # reparou pra equivalente → mede o ganho (separa a/d/reg)
                if args.audit is not None:  # DIAGNÓSTICO: o reparo S=1 preservou a TÉCNICA (AST) ou re-otimizou/reverteu?
                    sok, sreason = backend.technique_preserved(broken, repaired, original=raw)
                    audit.append({"query": qn, "outcome": outcome, "struct_ok": sok, "struct_reason": sreason,
                                  "broken": broken, "repaired": repaired, "original": raw})
                    tag = {True: "✓ técnica preservada", False: "✗ TÉCNICA MUDOU", None: "? não-parseável"}[sok]
                    print(f"   [audit] estrutural: {tag} — {sreason}")
                if args.no_gain:
                    cls, detail = "nogain", f"S=1 ({verified_on}); ganho não medido"; print("   → b→a? (s/ ganho)")
                elif orig_timed_out:
                    # original estoura no full + reparo equivalente (SF1). CONFIRMA que o reparo COMPLETA no full.
                    rh_full, rerr_full = backend.sample_hash(repaired)  # full, mesmo orçamento em que o original estourou
                    if rh_full and not rerr_full:
                        cls, detail = "a", f"orig estoura no full; reparo COMPLETA no full (equiv {verified_on}) → ganho≥floor"
                        print("   → ✅ b→a (reparo roda no full onde o original estoura)")
                    else:
                        cls, detail = "cand", f"equiv em {verified_on}; orig E reparo estouram no full ({_err_kind(rerr_full)})"
                        print(f"   → ⏱ b→a CANDIDATO (engine: reparo equivale no SF1 mas estoura no full [{_err_kind(rerr_full)}])")
                else:
                    try:
                        om = _robust_explain_analyze(backend, raw, timeout_s=budget).get("execution_time_ms")
                        rm = _robust_explain_analyze(backend, repaired, timeout_s=budget).get("execution_time_ms")
                    except Exception as e:
                        om = rm = None; cls, detail = "nogain", f"erro de medição: {e}"
                    if cls == "nogain": print(f"   → ganho: {detail}")
                    elif om is None: cls, detail = "a", "orig timeout no ANALYZE → ganho≥floor"; print("   → ✅ b→a")
                    elif rm is None: cls, detail = "b_unverif", "reparado deu timeout"; print("   → b→b (reparado timeout)")
                    else:
                        gain = (om - rm) / om * 100 if om else 0.0
                        detail = f"{om:.0f}ms→{rm:.0f}ms ({gain:+.1f}%)"; print(f"   ganho: {detail}")
                        land = float(os.getenv("GAIN_LAND_PCT", "5"))  # banda de ruído: |ganho|≤land = no-op (não é land nem regressão)
                        if gain > land: cls = "a"; print(f"   → ✅ b→a (LANDOU, {gain:+.1f}% > {land:.0f}%)")
                        elif gain < -land: cls = "reg"; print(f"   → ⚠️ b→reg ({gain:+.1f}% < -{land:.0f}%) — técnica contraproducente nesse plano (FIND-qualidade)")
                        else: cls = "d"; print(f"   → b→d (no-op, {gain:+.1f}% dentro de ±{land:.0f}% = ruído)")
            else:  # ainda b→b após reflexão — o SUBTIPO (cls) diz qual
                tip = {"b_nonequiv": "S≠1 PERSISTENTE → ADJUDICAR",
                       "b_exec": "erro de execução (escopo/mecânica) — coder não conseguiu escrever válido",
                       "b_dialeto": "⚠️ DIALETO/PG-ism → especializar coder por backend",
                       "b_revert": "coder reverteu/no-op — não soube reparar"}.get(cls, "")
                if cls == "b_nonequiv":  # prior-por-técnica (método 1) — ROTEIA o ADJUDICAR pelo prior
                    prior = _technique_prior(suggs)
                    detail = f"{detail} → {prior}"
                    tip = f"S≠1 PERSISTENTE → {prior}"
                    cls = "b_find" if "FIND-inválido" in prior else "b_wnoneq"  # FIND-inválido vs WRITE-duro (S≠1 c/ equiv garantida)
                print(f"   → 🚨 b→b [{cls}]: {tip}")
        print()
        buckets[cls] += 1
        rows.append((qn, outcome, cls, detail))

    n = len(cases)
    land = float(os.getenv("GAIN_LAND_PCT", "5"))  # limiar de land: |ganho|≤land = no-op (ruído), não conta como a/reg
    bt = buckets['b_find'] + buckets['b_wnoneq'] + buckets['b_exec'] + buckets['b_dialeto'] + buckets['b_revert'] + buckets['b_unverif']
    # ── VEREDITO: FIND ou WRITE? (princípio: FIND só é VÁLIDO se LANDA com GANHO > limiar; S=1 sozinho ≠ FIND válido) ──
    find_valido   = buckets['a']                                                 # S=1 + ganho>limiar → FIND real + WRITE era o gargalo
    find_nulo     = buckets['d'] + buckets['reg'] + buckets['reg_marg']           # S=1 mas no-op/regride → FIND∅/ruim (cosmético)
    write_fix_pend = buckets['cand'] + buckets['nogain']                          # S=1 alcançado, ganho NÃO medido → FIND PENDENTE
    write_duro    = buckets['b_exec'] + buckets['b_dialeto'] + buckets['b_revert'] + buckets['b_wnoneq']  # coder não escreveu equivalente
    find_invalido = buckets['b_find']                                            # b_nonequiv c/ prior CONDICIONAL → sem equivalente
    inverif       = buckets['b_unverif']
    answerable    = n - inverif
    pct = lambda x, d: f"{100*x/d:.0f}%" if d else "—"
    verdict = [
        f"VEREDITO — FIND ou WRITE? ({args.model} / {args.engine}, {n} botches de {nq} queries)",
        f"  ✅ FIND VÁLIDO (S=1 + ganho > {land:.0f}% = landou) : {find_valido}/{n} ({pct(find_valido,n)})   → FIND real, gargalo era o WRITE",
        f"  ∅  FIND NULO/RUIM (S=1, |ganho| ≤ {land:.0f}% ou pior) : {find_nulo}/{n}   → WRITE-fixável mas FIND cosmético/ruído (d/reg)",
        f"  ⏳ WRITE-fixável, FIND PENDENTE (s/ ganho) : {write_fix_pend}/{n}   → chegou a S=1; FALTA medir ganho p/ separar a de d (estágio 2)",
        f"  🔧 WRITE-duro (coder não escreveu válido) . : {write_duro}/{n} ({pct(write_duro,n)})   [exec {buckets['b_exec']} · revert {buckets['b_revert']} · dialeto {buckets['b_dialeto']} · S≠1-equiv-garant {buckets['b_wnoneq']}]",
        f"  ❌ FIND INVÁLIDO (sem equivalente) ........ : {find_invalido}/{n}   [b_nonequiv condicional, prior]",
        f"  ·  inverificável (orig timeout) ........... : {inverif}/{n}",
        f"  ➜ FIND raramente é o gargalo: só {find_invalido}/{answerable} inválido. O resto é WRITE — mas {write_fix_pend} PENDEM de ganho (estágio 2) pra separar a (WRITE) de d (FIND∅).",
    ]
    summary = [
        f"DETALHE ({n} botches distintos, de {nq} queries) — reparo com REFLEXÃO (≤{max_iters} tentativas do coder):",
        f"  ✅ b→a    (S=1 + ganho > {land:.0f}% = LANDOU)   : {buckets['a']}/{n}  ← FIND real (gargalo = WRITE)",
        f"  ∅  b→d    (S=1, |ganho| ≤ {land:.0f}% = no-op)   : {buckets['d']}/{n}  ← FIND cosmético / dentro do ruído",
        f"  ⚠️ b→reg  (S=1, regride < -{land:.0f}%)          : {buckets['reg']}/{n}",
        f"  ⏳ S=1 alcançado, ganho NÃO medido        : {buckets['cand']+buckets['nogain']}/{n}  ← PENDENTE (cand {buckets['cand']} + nogain {buckets['nogain']})",
        f"  🚨 b→b    (não resgatou)                  : {bt}/{n}, por TIPO:",
        f"        · FIND-inválido (prior condicional): {buckets['b_find']}",
        f"        · WRITE-duro: S≠1 c/ equiv garantida: {buckets['b_wnoneq']}",
        f"        · WRITE-duro: erro exec/escopo     : {buckets['b_exec']}",
        f"        · WRITE-duro: DIALETO/PG-ism       : {buckets['b_dialeto']}",
        f"        · WRITE-duro: revert/no-op         : {buckets['b_revert']}",
        f"        · inverificável (orig timeout)     : {buckets['b_unverif']}",
    ]
    print("=" * 60)
    for ln in verdict:
        print(ln)
    print("-" * 60)
    for ln in summary:
        print(ln)
    print("  → o b→b 'S≠1 persistente' é o resíduo a ADJUDICAR (FIND-inválido vs WRITE-duro: prior-por-técnica + reconstrução).")

    if args.md:
        if args.md != "AUTO":
            path = args.md
        else:  # mapeia o label do modelo pra estrutura de pastas existente (on/off do qwen)
            _fam = {"qwen-reasoning": "qwen/reasoning off",          # bare = reasoning OFF (trace só tem SQL, sem CoT)
                    "qwen-reasoning+nothink": "qwen/reasoning off",  # off explícito (futuro, +nothink)
                    "qwen-reasoning+think": "qwen/reasoning on",     # reasoning ON (CoT ~9.5k chars)
                    }.get(args.model, args.model.replace("+", "_").replace("/", "_"))
            # raw/ = diagnóstico sobre os resultados CRU/unaided (a sonda lê os class-b das runs unaided);
            # 'with find-tips + coder/' é reservado pro sistema AIDED/integrado (FIND-tips + coder), futuro.
            path = f"documentation/experiments/raw/{_fam}/{args.engine}/repair_probe.md"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        coder_name = os.getenv("CORRECTOR_MODEL") or os.getenv("FORMALIZER_MODEL", "qwen2.5-coder:7b")
        md = [f"# Repair-probe (degrau 2) — {args.model} / {args.engine}",
              f"> coder={coder_name} (LOCAL), reflexão ≤{max_iters} · gabarito quente reusado por query · `scripts/test_corrector_feasibility.py`.",
              "> b→{a,d,reg,b}: o coder local conserta o WRITE dos class-b? (preserva-estratégia + S=1 + ganho). b→b por subtipo.",
              "", "## " + verdict[0], ""] + [v.strip() for v in verdict[1:]] + [
              "", "## Por botch", "", "| query | outcome orig | pós-reparo | detalhe |", "|---|---|---|---|"]
        _lab = {"a": "✅ b→a (landou)", "d": "∅ b→d (no-op)", "reg": "⚠️ b→reg (regride muito)", "reg_marg": "? b→reg marginal",
                "nogain": "⏳ S=1, ganho pendente", "cand": "⏳ S=1 (SF1), ganho pendente (full estoura)",
                "b_find": "❌ b→b FIND-inválido (condicional)", "b_wnoneq": "🔧 b→b WRITE-duro (S≠1, equiv garantida)",
                "b_exec": "🔧 b→b WRITE-duro (erro exec/escopo)", "b_dialeto": "🔧 b→b WRITE-duro (DIALETO/PG-ism)",
                "b_revert": "🔧 b→b WRITE-duro (revert/no-op)", "b_unverif": "· b→b inverificável"}
        for qn, outcome, cls, detail in rows:
            md.append(f"| {qn} | {outcome} | {_lab.get(cls, cls)} | {detail} |")
        md += ["", "## " + summary[0], ""] + [s.strip() for s in summary[1:]]
        open(path, "w").write("\n".join(md) + "\n")
        print(f"\n📝 salvo em {path}")

    if args.audit is not None:
        os.makedirs(os.path.dirname(args.audit) or ".", exist_ok=True)
        with open(args.audit, "w") as fh:
            for rec in audit:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        nS1 = len(audit)
        bad = [r for r in audit if r["struct_ok"] is False]
        unp = [r for r in audit if r["struct_ok"] is None]
        ok = nS1 - len(bad) - len(unp)
        print("\n" + "=" * 60)
        print(f"[AUDITORIA ESTRUTURAL] {nS1} reparos S=1 (passaram o gate Jaccard):")
        print(f"  ✓ técnica preservada (AST) : {ok}/{nS1}")
        print(f"  ✗ TÉCNICA MUDOU            : {len(bad)}/{nS1}  ← Jaccard passou MAS estrutura reprova (re-otimização)")
        print(f"  ? não-parseável (fallback) : {unp}/{nS1}" if False else f"  ? não-parseável (fallback) : {len(unp)}/{nS1}")
        if bad:
            print("  divergências (query — motivo):")
            for r in bad:
                print(f"    · {r['query']}: {r['struct_reason']}")
        print(f"  → se ✗ for ALTO, o gate Jaccard estava frouxo (deixou re-otimização passar) → refazer com gate estrutural.")
        print(f"📝 pares salvos em {args.audit} (inspeção manual)")


if __name__ == "__main__":
    main()
