#!/usr/bin/env python3
"""Paired flash x pro comparison, GENERATED from the stores, in the canonical per-query format.

WHY GENERATED AND NOT WRITTEN BY HAND
    Twice on 18/08 a hand-read of these stores produced a false alarm, both times by treating a
    LOWER BOUND (the original timed out: "at least X%") as a MEASURED gain. Here the two never share
    a column, and the doc is recomputed on every call so it cannot drift from the data.

COLUMNS — the canonical template already used by the other result docs, plus one correction:
    the inference column now includes the WRITER. The old docs reported `total_inference_ms`, which
    sums only the LOCAL components — and the writer is ~4.6 min of a ~10 min run, so that number was
    hiding most of the LLM time. Both are printed, so old and new docs stay comparable.

USAGE
    python scripts/gen_writer_comparison.py --out documentation/experiments/final/p2/writer_flash_vs_pro.md
"""
import argparse
import glob
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, ".")
from src.pipeline.discovery.suggestion_store import _query_hash  # noqa: E402

ARMS = [("flash", "final_workload_flash_norules"), ("pro", "final_workload_pro_norules")]
PROBE_QUERIES = ["q1", "q7", "q27", "q63"]
ABBR = {"rewrite_correct": "L", "no_gain": "ng", "mechanics_failed": "mf",
        "timeout": "to", "unverified": "un", "equivalence_failed": "eq"}


def qnames():
    out = {}
    for f in glob.glob("queries/*/*/*.json"):
        try:
            d = json.load(open(f))
            s = d.get("sql") or d.get("query") or ""
            if s:
                out[_query_hash(s)[:8]] = os.path.basename(f).replace(".json", "")
        except Exception:
            pass
    return out


def load_runs(store, names):
    per = defaultdict(list)
    for f in glob.glob(f".cache/suggestions_{store}/*.json"):
        try:
            r = json.load(open(f))
        except Exception:
            continue
        if r.get("writer"):
            per[names.get(r["query_hash"][:8], "?")].append(r)
    for v in per.values():
        v.sort(key=lambda x: x["timestamp"])
    return per


def load_indexes(store, names):
    per = defaultdict(list)
    for f in glob.glob(f".cache/index_{store}/*.json"):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        per[names.get(d["query_hash"][:8], "?")].extend(d.get("recommendations") or [])
    return per


def technique(runs):
    """What the FIND decided — the bolded technique names inside the strategy. Falls back to the
    writer-facing suggestions when the run carries no strategy (single-agent cells)."""
    c = Counter()
    for r in runs:
        found = re.findall(r"\*\*(.+?)\*\*", r.get("strategy") or "")
        for t in found[:2]:
            c[t.strip()] += 1
        if not found:
            for s in (r.get("suggestions") or [])[:1]:
                c[s.strip()[:34]] += 1
    return " · ".join(t for t, _ in c.most_common(2)) or "—"


def gain(runs):
    lands = [x for x in runs if x["outcome"] == "rewrite_correct"]
    if not lands:
        return "não"
    med = [x["metrics"]["execution_time"].get("improvement_pct") for x in lands]
    med = [m for m in med if m is not None]
    if med:
        return f"**sim** {max(med):.1f}%"
    lb = [x["metrics"]["execution_time"].get("improvement_lower_bound_pct") for x in lands]
    lb = [m for m in lb if m is not None]
    prov = any(x.get("float_sensitive_sf1_only") for x in lands)
    if lb:
        return f"**sim** ≥{max(lb):.1f}%" + (" ⚠️prov." if prov else "")
    return "**sim** (sem número)"


def infer(runs):
    """Median TOTAL LLM time per run, and how much of it is the writer."""
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
    s = f"{statistics.median(tot):.0f}s"
    if wr:
        s += f" (writer {statistics.median(wr):.0f}s)"
    return s


def index_cells(recs):
    """(single index, workload impact) in the canonical shape."""
    if not recs:
        return "—", "—"
    def benefit(r):
        m = r.get("measured_read_benefit_pct")
        return m if m is not None else (r.get("estimated_benefit_pct") or 0)
    best = max(recs, key=benefit)
    tag = "medido" if best.get("measured_read_benefit_pct") is not None else "estimado"
    single = f"{best.get('table')}.{best.get('column')} {benefit(best):.1f}% ({tag})"
    aggs = [r.get("workload_aggregate_benefit_pct") for r in recs
            if r.get("workload_aggregate_benefit_pct") is not None]
    regr = [r.get("workload_queries_regressed") or 0 for r in recs]
    wl = f"agg {max(aggs):.1f}% ({max(regr) if regr else 0} regr)" if aggs else "—"
    return single, wl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    names = qnames()
    data = {tag: (load_runs(store, names), load_indexes(store, names)) for tag, store in ARMS}

    ts = [x["timestamp"] for runs, _ in data.values() for v in runs.values() for x in v]
    window = f"{min(ts)[:16]} → {max(ts)[:16]}" if ts else "(sem runs)"

    L = []
    L.append("# Sonda 0b — o writer: `flash` × `pro`\n")
    L.append(f"**Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}** por "
             "`scripts/gen_writer_comparison.py` · **janela das runs:** " + window + "\n")
    L.append("> ⚠️ **Tabelas geradas do store.** Não editar à mão — regenerar. A prosa é humana.\n")

    L.append("## Por que este teste existe\n")
    L.append("A decisão de trocar o writer para o `flash` economiza **~US$ 19** no que falta da "
             "campanha. Só que a comparação `flash`×`pro` em que essa decisão se apoiava foi medida "
             "**com regras ON e com o masking vazando** — uma configuração que não entregamos mais. "
             "Decidir o writer com ela seria decidir com base num sistema que não existe.\n")
    L.append("Esta sonda refaz a comparação **na composição que o P2 entrega**: regras OFF, masking "
             "corrigido, plano ON, índice validado por execução. Uma variável muda — o tier do "
             "writer. Ela roda **antes** das células caras porque o writer é componente da "
             "arquitetura: escolher errado obrigaria a refazer tudo que vier depois.\n")
    L.append("⚠️ **É sonda, não resultado.** 4 queries não viram tabela de paper; o número publicável "
             "é o da célula cheia (21 queries). O papel dela é decidir, não medir.\n")

    for tag, store in ARMS:
        runs, idx = data[tag]
        L.append(f"## `{tag}`\n")
        got = {q: runs.get(q) for q in PROBE_QUERIES if runs.get(q)}
        if not got:
            L.append("_ainda não rodou._\n")
            continue
        L.append("| q | desfecho | melhoria | inferência LLM (med) | técnica do FIND | índice (single) | índice workload |")
        L.append("|---|---|---|---|---|---|---|")
        for q in PROBE_QUERIES:
            v = runs.get(q)
            if not v:
                L.append(f"| `{q}` | — não rodou — | | | | | |")
                continue
            desf = " · ".join(ABBR.get(x["outcome"], x["outcome"][:2]) for x in v)
            si, wl = index_cells(idx.get(q))
            L.append(f"| `{q}` | {desf} | {gain(v)} | {infer(v)} | {technique(v)} | {si} | {wl} |")
        L.append("")

    L.append("## Como ler\n")
    L.append("- **desfecho**: um por run, em ordem. `L` land · `ng` sem ganho · `mf` falha mecânica · "
             "`to` timeout · `eq` falha de equivalência.\n")
    L.append("- **melhoria**: `sim` só quando houve land COM ganho. Um **`≥`** é **limite inferior** — "
             "a original não completa em escala real, então o ganho verdadeiro é MAIOR. "
             "`⚠️prov.` = land **provisório**: equivalência verificada em escala reduzida, que "
             "**refuta mas não certifica**.\n")
    L.append("> ⛔ **Nunca comparar `≥X%` com `X%`** como se fossem a mesma régua. Foi essa confusão "
             "que gerou dois alarmes falsos em 18/08.\n")
    L.append("- **inferência**: tempo TOTAL de LLM por run (mediana), com a parte do writer "
             "destacada. ⚠️ Os docs antigos reportavam só `total_inference_ms`, que soma apenas os "
             "componentes **locais** — e o writer é ~46% do relógio. O número daqui é maior porque "
             "está **mais completo**, não porque o sistema ficou mais lento.\n")
    L.append("- **índice**: `(medido)` = índice criado de verdade e cronometrado; `(estimado)` = "
             "custo do planner. As células anteriores a 18/08 12:28 são todas estimadas.\n")

    txt = "\n".join(L)
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        open(a.out, "w").write(txt)
        print(f"wrote {a.out}")
    else:
        print(txt)


if __name__ == "__main__":
    main()
