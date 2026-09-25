#!/usr/bin/env python3
"""Generate a cell's individual doc FROM THE STORE — never transcribed by hand.

WHY: every analysis error in this campaign came from hand-reading the stores. Twice I confused a
LOWER BOUND (the original timed out, so the gain is "at least X%") with a MEASURED gain, and reported
a collapse that did not exist. Once I paired two cells that were not a pair. Generating the tables
from the data removes that whole class of mistake, and makes the doc regenerable when a cell grows.

It emits the canonical template's factual sections; the narrative (conclusion at the top, threats)
stays human — but it can never disagree with the data.

USAGE
    python scripts/gen_cell_doc.py <store> [--out path.md]
"""
import argparse
import glob
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")
from src.pipeline.discovery.suggestion_store import _query_hash  # noqa: E402


def query_names():
    names = {}
    for f in glob.glob("queries/*/*/*.json"):
        try:
            d = json.load(open(f))
            sql = d.get("sql") or d.get("query") or ""
            if sql:
                names[_query_hash(sql)[:8]] = os.path.basename(f).replace(".json", "")
        except Exception:
            pass
    return names


def query_benchmarks():
    """hash -> benchmark, lido do DIRETÓRIO (`queries/<split>/<BENCH>/…`), não do nome da query.

    Por que existe (26/08): nenhum doc de `final/p1` dizia de QUAL dataset e QUAL banco tratava —
    era preciso abrir o store para descobrir. Com TPC-DS e JOB/IMDb rodando em Postgres e MySQL, são
    quatro combinações, e um doc sem selo é um convite a comparar coisas de células diferentes.
    ⛔ Não inferir o benchmark pela CONTAGEM de queries (13 vs 21): uma célula incompleta de TPC-DS
    tem 13 queries e seria rotulada IMDb.
    """
    out = {}
    for f in glob.glob("queries/*/*/*.json"):
        try:
            d = json.load(open(f))
            sql = d.get("sql") or d.get("query") or ""
            if sql:
                bench = os.path.basename(os.path.dirname(f)).upper()
                out[_query_hash(sql)[:8]] = "IMDb/JOB" if bench == "JOB" else "TPC-DS"
        except Exception:
            pass
    return out




def load_index(cell, names):
    """Recomendações de índice por query, do store de índice da célula."""
    per = defaultdict(list)
    for f in glob.glob(f".cache/index_{cell}/*.json"):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        per[names.get(d["query_hash"][:8], "?")].extend(d.get("recommendations") or [])
    return per


def _fmt_ms(ms):
    """Time in SECONDS, as the user asked; sub-second stays in ms so a 0.2s query is not printed as 0.0s."""
    if ms is None:
        return "—"
    return f"{ms:.0f} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def before_after(runs, lands):
    """ORIGINAL -> REWRITTEN wall time for the best land — the number the % is computed FROM.

    Reporting only the delta hides which regime a query is in: +85% on a 0.2s query and +85% on a 17s
    query are different findings, and the reader cannot tell them apart from the percentage alone.
    Standing instruction from the user (and now applied to the generated tables too).
    """
    if lands:
        best, best_gain = None, None
        for x in lands:
            et = ((x.get("metrics") or {}).get("execution_time") or {})
            g = et.get("improvement_pct")
            if g is not None and (best_gain is None or g > best_gain):
                best, best_gain = et, g
        if best is None:                       # provisional land: only a lower bound exists
            best = ((lands[0].get("metrics") or {}).get("execution_time") or {})
        o = best.get("original_ms") or best.get("original_floor_ms")
        n = best.get("optimized_ms")
        mark = " (floor)" if best.get("original_ms") is None and best.get("original_floor_ms") else ""
        if o or n:
            return f"{_fmt_ms(o)} → {_fmt_ms(n)}{mark}"
    # No land: still report what the ORIGINAL costs, so the reader can tell a hard-for-the-model
    # query (fast original) from a genuinely heavy one.
    # ⚠️ Guarded access: a `timeout` record has no measurement block at all (the client gave up before
    # the server answered), so `metrics` / `execution_time` can be missing or None.
    origs = []
    for x in runs:
        et = ((x.get("metrics") or {}).get("execution_time") or {})
        o = et.get("original_ms") or et.get("original_floor_ms")
        if o:
            origs.append(o)
    if origs:
        import statistics
        return f"{_fmt_ms(statistics.median(origs))} → —"
    return "—"


def index_cells(recs):
    """(melhor índice, impacto no workload).

    ⛔ SÓ REPORTA NÚMERO QUANDO ELE FOI MEDIDO POR EXECUÇÃO (04/09, pedido da usuária).

    A versão anterior caía para a estimativa do planner em silêncio — o número aparecia com a MESMA
    tipografia do medido, e a única diferença era a palavra "(estimado)" no fim da célula. Isso fez
    com que docs inteiros de MySQL exibissem percentuais de ganho de índice que **nunca foram
    cronometrados**: as 19 células MySQL do projeto têm 100% de `validation: estimated` (ou nem o
    campo, nas anteriores a 18/08), porque o caminho "cria o índice e EXECUTA" só existia no backend
    PostgreSQL até 03/09.

    ⭐ POR QUE NÃO BASTA ROTULAR. Os próprios dados do PG refutam a estimativa: em 448 recomendações
    com os DOIS números, **35-38% discordam no SINAL** quando o efeito real passa de 5% — o planner
    prevê ganho onde o cronômetro mede perda (há um caso de `planner +2,6% × real -167,3%`). Um
    número que erra a DIREÇÃO em mais de um terço dos casos não é um número fraco: não é um número.

    Então a estimativa deixa de virar percentual no doc. Fica o aviso, que preserva a informação de
    que houve recomendação de índice sem oferecer um valor que não se sustenta.
    """
    if not recs:
        return "—", "—"
    medidos = [r for r in recs if r.get("measured_read_benefit_pct") is not None]
    if not medidos:
        n = len(recs)
        return (f"{n} recommendation(s), **none measured** — planner estimate only", "—")
    best = max(medidos, key=lambda r: r["measured_read_benefit_pct"])
    single = f"{best.get('table')}.{best.get('column')} {best['measured_read_benefit_pct']:.1f}% (measured)"
    if len(medidos) < len(recs):
        single += f" · {len(recs)-len(medidos)} of {len(recs)} without measurement (omitted)"
    aggs = [r.get("workload_aggregate_benefit_pct") for r in recs
            if r.get("workload_aggregate_benefit_pct") is not None]
    regr = [r.get("workload_queries_regressed") or 0 for r in recs]
    wl = f"agg {max(aggs):.1f}% ({max(regr) if regr else 0} regr)" if aggs else "—"
    return single, wl


def technique(runs):
    """O que o FIND DECIDIU — nomes em negrito na `strategy`. Cai para `suggestions` no braço raw,
    onde não há estratégia externalizada (1 agente)."""
    c = Counter()
    for r in runs:
        found = re.findall(r"\*\*(.+?)\*\*", r.get("strategy") or "")
        for t in found[:2]:
            c[t.strip()[:30]] += 1
        if not found:
            for sg in (r.get("suggestions") or [])[:1]:
                c[sg.strip()[:30]] += 1
    return " · ".join(t for t, _ in c.most_common(2)) or "—"


def infer_ms(runs):
    """Tempo TOTAL de LLM por run (mediana), com a parte do writer destacada. ⚠️ Os docs ANTIGOS
    reportavam só `total_inference_ms`, que soma apenas os componentes LOCAIS — e o writer é ~46% do
    relógio. O número daqui é maior porque está mais COMPLETO, não porque o sistema ficou lento."""
    tot, wr = [], []
    for r in runs:
        m = r.get("metrics") or {}
        t = (m.get("total_inference_ms") or 0) + (m.get("writer_inference_ms") or 0)
        if t:
            tot.append(t / 1000)
        if m.get("writer_inference_ms"):
            wr.append(m["writer_inference_ms"] / 1000)
    if not tot:
        return "—"
    out = f"{statistics.median(tot):.0f}s"
    if wr:
        out += f" (w {statistics.median(wr):.0f}s)"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("store")
    ap.add_argument("--out")
    # LEITURA DE PAINEL (25/08). A mesma célula serve a duas leituras: a ESCADA da manchete lê todos
    # os runs (n=5), o PAINEL cross-model lê os N primeiros por query, para casar a régua com as
    # células que estão a n=3. Cronológico e não aleatório: os N primeiros são exatamente os que já
    # existiam antes de um topup, então completar uma célula NÃO muda a leitura do painel.
    ap.add_argument("--first-n", type=int, default=None,
                    help="use only the first N REAL runs per query (panel reading)")
    # FILTRO DE REGIME (25/08). Alguns stores guardam MAIS DE UMA célula: `tpcds_mysql` tem o braço
    # plan-ON (`+nohw`) e o `noplan` no mesmo diretório, e `p1_llama_raw` tem 63 runs `noplan` mais
    # UM plan-ON. Ler sem filtrar mistura regimes — comparação inválida pela regra de setup.
    ap.add_argument("--model-contains", default=None,
                    help="only runs whose `model` field contains this string (e.g. noplan)")
    # ⚠️ EXCLUSÃO É NECESSÁRIA porque os rótulos de regime são PREFIXOS uns dos outros:
    # `+nohw` é substring de `+nohw+noplan`, então `--model-contains +nohw` casa os DOIS braços.
    # Para isolar o plan-ON: `--model-contains +nohw --model-excludes noplan`.
    ap.add_argument("--model-excludes", default=None,
                    help="drop runs whose `model` contains this string")
    a = ap.parse_args()
    names = query_names()

    per = defaultdict(list)
    _excl = {}
    for p in glob.glob(f".cache/suggestions_{a.store}/*.json"):
        try:
            r = json.load(open(p))
        except Exception:
            continue
        # ⚠️ DO NOT filter on `writer` here. In RAW cells there is no writer by construction (one
        # model does everything), so dropping writer-less records silently empties the whole cell —
        # the exact mistake that made me report p1_deepseek_raw as an 18-query cell on 18/08.
        # The real "the pipeline never ran" marker is the absence of an outcome.
        if not r.get("outcome"):
            continue
        if a.model_excludes and a.model_excludes in (r.get("model") or ""):
            _excl[r.get("model") or "?"] = _excl.get(r.get("model") or "?", 0) + 1
            continue
        if a.model_contains and a.model_contains not in (r.get("model") or ""):
            _excl[r.get("model") or "?"] = _excl.get(r.get("model") or "?", 0) + 1
            continue
        per[r["query_hash"][:8]].append(r)
    if not per:
        sys.exit(f"no runs in .cache/suggestions_{a.store}")

    # ⚠️ RUN REAL × TIMEOUT DE BASELINE (corrigido 25/08). O cabeçalho contava TODO registro com
    # `outcome` como run — inclusive os `timeout`, em que o cliente desistiu e o pipeline nunca
    # rodou. `p1_qwen_raw` saía com "89 runs" tendo 62; `p1_deepseek_raw` saía como 21 queries
    # completas tendo TRÊS sem dado nenhum. Regra idêntica à do `_done_counts` (run_matrix.py),
    # que é quem o `--resume` obedece.
    def _is_real(r):
        return not (r.get("outcome") == "timeout" and not r.get("writer"))

    if a.first_n:
        # mantém em ordem cronológica até juntar N runs REAIS; os timeouts do caminho ficam,
        # porque fazem parte do que aquela tentativa custou
        for q, v in list(per.items()):
            kept, n_real = [], 0
            for r in sorted(v, key=lambda x: x.get("timestamp") or ""):
                if n_real >= a.first_n:
                    break
                kept.append(r)
                if _is_real(r):
                    n_real += 1
            per[q] = kept

    n_real = sum(1 for v in per.values() for x in v if _is_real(x))
    n_to = sum(1 for v in per.values() for x in v if not _is_real(x))

    first = next(iter(per.values()))[0]
    # ⛔ Do NOT read the writer from ONE record: if the first record of the first query is a baseline
    # timeout it has no writer, and the whole cell gets labelled RAW (this bit both `luna` cells,
    # which the paper calls aided-cloud). The label is the MOST FREQUENT writer among runs that have one.
    _ws = Counter(r["writer"] for v in per.values() for r in v if r.get("writer"))
    _arm = _ws.most_common(1)[0][0] if _ws else "RAW (one model does everything — no separate writer)"
    _bm = query_benchmarks()
    _b = Counter(_bm.get(h) for h in per if _bm.get(h))
    _bench = " + ".join(sorted(_b)) if _b else "?"
    _eng = Counter((x.get("db_type") or "?") for v in per.values() for x in v).most_common(1)[0][0]
    # ⚠️ `arm` no store vale só "raw" ou "aided" — NÃO distingue aided-LOCAL de aided-CLOUD, e essa
    # é justamente a diferença entre o L4 (writer local, tese leve/local) e o C1 (writer de nuvem).
    # Em 26/08 a usuária leu "C1 fechado" como "aided-local pronto". O selo passa a carregar o WRITER.
    _armv = Counter((x.get("arm") or "?") for v in per.values() for x in v).most_common(1)[0][0]
    _wr = Counter((x.get("writer") or "") for v in per.values() for x in v).most_common(1)[0][0]
    if _armv == "aided":
        _armv = "aided-cloud" if _wr.startswith("cloud") else ("ceiling" if "cloudfind" in (first.get("model") or "") else "aided-local")
    _wtxt = f" · writer `{_wr}`" if _wr else " · writer: — (raw, one model does everything)"
    _nmode = Counter(sum(1 for x in v if _is_real(x)) for v in per.values()).most_common(1)[0][0]
    _rules = "WITH rules" if any(x.get("apply_mode") for v in per.values() for x in v) else "no rules"

    out = [f"# Cell `{a.store}` — generated by `scripts/gen_cell_doc.py`",
           "",
           f"## `{_bench}` · `{str(_eng).upper()}` · arm **`{_armv}`**{_wtxt} · n={_nmode} · {_rules}",
           "",
           "> **This label identifies the cell.** Four dataset × engine combinations coexist in P1"
           " (TPC-DS/PG · TPC-DS/MySQL · IMDb/PG · IMDb/MySQL) — **never compare rows from cells with"
           " different labels** without stating in the text that the comparison is cross-engine or cross-benchmark.",
           "",
           "> **The tables below are GENERATED from the store.** Do not hand-edit — regenerate. The narrative",
           "> (conclusion at the top, threats to validity) is human and goes ABOVE this line.",
           "",
           f"**Setup recorded in the data:** FIND `{first.get('model')}` · WRITE `{_arm}` · "
           f"rules `{'ON' if first.get('apply_mode') else 'off'}` · engine `{first.get('db_type')}` · "
           f"arm `{first.get('arm')}`",
           ""]

    ts = sorted(x["timestamp"] for v in per.values() for x in v)
    _hdr = (f"**Window:** {ts[0][:16]} → {ts[-1][:16]} · **{n_real} REAL runs** across "
            f"**{len(per)} queries**")
    if n_to:
        _hdr += (f" · **+{n_to} baseline timeouts** (the client gave up before the pipeline ran — "
                 f"**not runs**, and `--resume` ignores them)")
    if a.first_n:
        _hdr += (f"\n\n> **PANEL READING: only the first {a.first_n} runs per query.** "
                 f"Do not compare the reach here with that of a cell read in full — more runs "
                 f"gives more chance of landing ≥1, and the bias is mechanical.")
    # ⚠️ QUERY SEM DADO NENHUM (25/08). "21 queries" mente quando três delas só têm timeout: foi
    # exatamente assim que `p1_deepseek_raw` ficou documentado como painel fechado tendo q63/q67/q85
    # em zero. O contador de queries não pode ser o único número visível.
    _vazias = sorted(names.get(q, q) for q, v in per.items() if not any(_is_real(x) for x in v))
    _curtas = sorted(names.get(q, q) for q, v in per.items()
                     if 0 < sum(1 for x in v if _is_real(x)) < (a.first_n or 3))
    if _excl:
        _hdr += ("\n\n> **Regime filter `--model-contains " + str(a.model_contains)
                 + "`** — excluded from this doc: "
                 + ", ".join(f"`{k}` ({v} runs)" for k, v in sorted(_excl.items()))
                 + ". They are in the SAME store and are **a different cell**; reading without filtering mixes regimes.")
    if _vazias:
        _hdr += (f"\n\n> **{len(_vazias)} QUERY(IES) WITH NO REAL RUN AT ALL: {', '.join(_vazias)}** — only "
                 f"timeout. **The cell is NOT closed**, no matter what the query count suggests.")
    if _curtas:
        _hdr += (f"\n\n> **Below target:** {', '.join(_curtas)}.")
    out += [_hdr, ""]

    # ⚠️ TABELA CANÔNICA — o mesmo formato dos docs de experimento (`full_pipeline_*.md`), pedido
    # pela usuária em 23/08: "quero uma tabelona com índice, reescrita... está muito texto e pouco
    # dado". Colunas: desfecho por run · melhoria · inferência LLM · técnica do FIND · índice.
    ABBR = {"rewrite_correct": "L", "no_gain": "ng", "mechanics_failed": "mf", "timeout": "to",
            "unverified": "un", "equivalence_failed_semantic": "eq", "equivalence_failed_structural": "eqs"}
    idx = load_index(a.store, names)
    out += ["## Per-query table",
            "",
            "| q | outcome (per run) | before → after | improvement | LLM inference (med) | FIND technique | index (best) | workload index |",
            "|---|---|---|---|---|---|---|---|"]
    prov = []
    for h, v in sorted(per.items(), key=lambda x: names.get(x[0], x[0])):
        q = names.get(h, h)
        v = sorted(v, key=lambda x: x["timestamp"])
        lands = [x for x in v if x["outcome"] == "rewrite_correct"]
        med = [x["metrics"]["execution_time"].get("improvement_pct") for x in lands]
        med = [m for m in med if m is not None]
        lb = [x["metrics"]["execution_time"].get("improvement_lower_bound_pct") for x in lands]
        lb = [m for m in lb if m is not None]
        if lands and not med and lb:
            prov.append(q)
        if med:
            gain = f"**yes** {max(med):.1f}%"
        elif lb:
            gain = f"**yes** ≥{max(lb):.1f}% prov."
        elif lands:
            gain = "**yes** (no number)"
        else:
            gain = "no"
        desf = " · ".join(ABBR.get(x["outcome"], x["outcome"][:3]) for x in v)
        single, wl = index_cells(idx.get(q))
        out.append(f"| `{q}` | {desf} | {before_after(v, lands)} | {gain} | {infer_ms(v)} "
                   f"| {technique(v)} | {single} | {wl} |")
    out += ["",
            "**Legend** — outcome: `L` land · `ng` no gain · `mf` mechanical failure · `to` timeout · "
            "`eq`/`eqs` semantic/structural non-equivalence · `un` unverifiable.",
            "**≥X%** is a **lower bound** (the original doesn't complete at real scale → the true gain is "
            "LARGER). `prov.` = **provisional** land, verified at reduced scale.",
            "**Never compare `≥X%` with `X%`** — they are not the same ruler.",
            "**before → after**: wall time of the ORIGINAL and of the REWRITE on the best land (this is where the % comes from). With no land, shows the median of the original — to separate a *query heavy for the DATABASE* from a *query hard for the MODEL*. `(floor)` = the original didn't complete, the time is the budget FLOOR.",
            "**inference**: TOTAL LLM time per run (median), with the writer's share highlighted.",
            "**index (best)**: `(measured)` = index actually created in a transaction, timed "
            "(warm-up discarded + median of 3) and undone by ROLLBACK · `(estimated)` = planner cost.",
            "**The `workload index` column is ALWAYS ESTIMATED**, even when the one next to it is measured: it is the "
            "average of `(baseline_cost − cost_with_index)/baseline_cost` over the OTHER queries in the record — "
            "**planner cost, not time**. `agg X% (N regr)` = the others get X% cheaper on average, "
            "N get worse. **Do not read the two columns on the same ruler** — the planner overestimates "
            "(seen: 10.4% estimated × 0.8% measured on the same index).",
            "**prov.**: the ORIGINAL doesn't complete within budget → no measured gain (only a lower bound) AND the "
            "equivalence check falls back to reduced scale, which **refutes but does not certify**. Both things have the "
            "same cause.",
            ""]

    o = Counter(x["outcome"] for v in per.values() for x in v)
    reach = sum(1 for v in per.values() if any(x["outcome"] == "rewrite_correct" for x in v))
    definitive = sum(1 for v in per.values()
                     if any(x["outcome"] == "rewrite_correct" and not x.get("float_sensitive_sf1_only") for x in v))
    out += ["", "## Aggregate", "",
            f"- **reach {reach}** of {len(per)} queries · **{definitive} fully verified** · "
            f"**{reach - definitive} provisional**" + (f" ({', '.join(prov)})" if prov else ""),
            f"- outcomes: {dict(o.most_common())}",
            "",
            "> **ALWAYS report both numbers** — *\"X validated, of which N provisional "
            "(verified at reduced scale because the original doesn't complete at real scale), "
            "and X−N fully verified\"*. And **never** compare `≥X%` (lower bound) with "
            "`X%` (measured) as if they were the same metric.",
            ""]

    txt = "\n".join(out)
    if a.out:
        # ⚠️ PRESERVAR A NARRATIVA HUMANA (correção 20/08). O cabeçalho manda escrever a prosa ACIMA
        # da linha de aviso, mas a primeira versão sobrescrevia o arquivo inteiro — toda regeneração
        # apagaria a conclusão e as ameaças à validade, que é justamente a parte que não se recupera.
        # Agora tudo o que estiver antes do marcador é mantido; só as tabelas são refeitas.
        MARK = "<!-- GENERATED BELOW — do not edit; regenerate with scripts/gen_cell_doc.py -->"
        prefixo = ""
        if os.path.exists(a.out):
            antigo = open(a.out).read()
            if MARK in antigo:
                prefixo = antigo.split(MARK)[0]
        # ⛔⛔ NUNCA concatenar narrativa à mão — este bloco JÁ preserva o texto humano.
        #
        #   Em 07-08/09 eu ignorei isto e montei a concatenação por fora, duas vezes, com dois danos:
        #     (a) `open(p,"w").write(narr + open(p).read())` — o modo "w" TRUNCA antes do read, então
        #         o read voltou VAZIO e 4 docs perderam a tabela inteira;
        #     (b) a correção às pressas concatenou a narrativa sobre um arquivo que já a continha —
        #         6 docs ficaram com a narrativa DUPLICADA.
        #   ⇒ A forma correta é uma só: `gen_cell_doc.py <store> --out <arquivo>`. O prefixo humano
        #     (tudo acima do MARK) é lido e reescrito automaticamente.
        conteudo = prefixo + MARK + "\n\n" + txt
        open(a.out, "w").write(conteudo)

        # ⛔ AUTOVERIFICAÇÃO (08/09) — o doc não sai daqui quebrado.
        #    Os dois danos acima passaram despercebidos porque nada conferia o arquivo depois de
        #    escrito; foram achados dias depois, por desconfiança da usuária sobre um número.
        _h1 = [l for l in conteudo.split("\n") if l.startswith("# ") and not l.startswith("# Cell")]
        _probs = []
        if len(_h1) > 1:
            _probs.append(f"DUPLICATE TITLE ({len(_h1)}x) — narrative concatenated more than once")
        if conteudo.count(MARK) != 1:
            _probs.append(f"GENERATED marker appears {conteudo.count(MARK)}x (expected 1)")
        if not any(l.startswith("| `") for l in conteudo.split("\n")):
            _probs.append("NO per-query TABLE — the generated body didn't make it into the file")
        if _probs:
            print(f"INCONSISTENT DOC — {a.out}")
            for _p in _probs:
                print(f"   · {_p}")
            print("   DO NOT use this doc. Regenerate with `gen_cell_doc.py <store> --out <file>`")
            print("      and NEVER concatenate the narrative by hand — the script already preserves it.")
            sys.exit(3)

        print(f"wrote {a.out}" + (" (narrative preserved)" if prefixo.strip() else ""))
    else:
        print(txt)


if __name__ == "__main__":
    main()
