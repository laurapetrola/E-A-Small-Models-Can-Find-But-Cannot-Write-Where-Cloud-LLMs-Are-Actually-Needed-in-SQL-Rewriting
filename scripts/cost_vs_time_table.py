#!/usr/bin/env python3
"""Cost vs. real-time divergence table — the "cost can't gate a rewrite" finding, straight from the store.

For every LANDED rewrite (outcome=rewrite_correct) where the ORIGINAL completed (so we have an original
cost AND an original time), this compares:
  - cost_delta%  = (optimized_cost - original_cost) / original_cost × 100   (positive = planner thinks WORSE)
  - time_gain%   = improvement_pct                                          (positive = actually FASTER)

The headline: rows where the planner cost says WORSE (cost_delta > 0) but the query is actually FASTER
(time_gain > 0) are rewrites a COST-BASED selector would have WRONGLY REJECTED — proof that you must gate
rewrite selection on REAL TIME (+ S=1), not on estimated cost. (Pairs with the S=1 finding: the two
shortcuts that fail — equivalence needs S=1, speed needs real time.)

Scope: LANDED (a) AND no-op/regression (d=no_gain) rewrites — the disagreement lives mostly in the d
runs (equivalent, but cost says one thing / real time another). Heavy queries whose ORIGINAL timed out
are NO LONGER skipped: the executor now captures the original's cost-only EXPLAIN (instant, no execution),
and the time uses the timeout LOWER BOUND (marked `≥`). A run is only dropped if even the cost is missing.

Usage: python scripts/cost_vs_time_table.py [--csv] [--md OUT]
"""
import argparse
import glob
import hashlib
import json
import os
import re

STORE = os.getenv("SUGGESTION_STORE_DIR", ".cache/suggestions")  # raw=default; aided seta a env


def _query_hash(sql):
    return hashlib.sha256(sql.encode()).hexdigest()[:12]


def _hash_to_stem():
    """Map each query_hash back to its file stem (q1, q38, …) so the table is readable."""
    m = {}
    for f in glob.glob("queries/**/*.json", recursive=True):
        if os.path.basename(f) == "workload_registry.json":
            continue
        try:
            m[_query_hash(json.load(open(f))["sql"])] = os.path.splitext(os.path.basename(f))[0]
        except Exception:
            continue
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--md", help="also write the markdown table to this path (else it only prints)")
    ap.add_argument("--model", default=None, help="filtra por modelo (ex.: llama+nothink) — para doc por-modelo")
    ap.add_argument("--engine", default=None, help="filtra por engine (postgres|mysql)")
    args = ap.parse_args()

    names = _hash_to_stem()
    rows = []
    misleads = 0
    for f in glob.glob(f"{STORE}/*.json"):
        try:
            r = json.load(open(f))
            r = r[0] if isinstance(r, list) else r
        except Exception:
            continue
        if args.engine and r.get("db_type") != args.engine: continue
        if args.model and r.get("model") != args.model: continue
        outcome = r.get("outcome")
        # Include LANDED (a) AND no-op/regression (d=no_gain): the cost≠time DISAGREEMENT lives mostly in
        # the d runs (equivalent rewrite, cost says one thing, real time another — e.g. q7: cost↓ / time↑).
        if outcome not in ("rewrite_correct", "no_gain"):
            continue
        cls = "a" if outcome == "rewrite_correct" else "d"
        m = r.get("metrics") or {}
        cost = m.get("planner_cost") or {}
        et = m.get("execution_time") or {}
        oc, opc = cost.get("original"), cost.get("optimized")
        # TIME: real % when measured; else the timeout LOWER BOUND (heavy originals q1/q30 — no longer
        # skipped now that the executor captures the original's cost-only EXPLAIN even on timeout).
        gain = et.get("improvement_pct")
        gain_is_lb = False
        if gain is None:
            gain = et.get("improvement_lower_bound_pct")
            gain_is_lb = gain is not None
        if oc in (None, 0) or opc is None or gain is None:
            continue
        cost_delta = (opc - oc) / oc * 100.0
        verdict = "agree"
        if cost_delta > 1.0 and gain > 1.0:
            verdict = "⚠ COST WOULD REJECT (cost↑, faster)"   # the finding
            misleads += 1
        elif cost_delta < -1.0 and gain < -1.0:
            verdict = "⚠ cost would ACCEPT (cost↓, slower)"
            misleads += 1
        rows.append([names.get(r.get("query_hash"), r.get("query_hash", "?")[:8]),
                     r.get("model") or "?", r.get("db_type") or "?", cls,
                     f"{cost_delta:+.1f}%", f"{'≥' if gain_is_lb else ''}{gain:+.1f}%", verdict])

    # Agrupa as runs da MESMA query juntas (ordem natural q1,q3,q5…), classe depois. (Antes era
    # "mislead-first", que espalhava as runs duplicadas — ruim de ler; e no PG não há mislead mesmo.)
    rows.sort(key=lambda x: (int(re.sub(r"\D", "", x[0]) or 0), x[0], x[3]))
    header = ["query", "model", "engine", "class", "cost_delta", "time_gain", "verdict"]
    if args.csv:
        print(",".join(header))
        for row in rows:
            print(",".join(str(x) for x in row))
        return
    if not rows:
        print("(no landed run with an original baseline yet — run the matrix; timeout-only queries are skipped)")
        return
    table = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    table += ["| " + " | ".join(str(x) for x in row) + " |" for row in rows]
    footer = (f"\n{misleads}/{len(rows)} rewrites where the planner COST disagrees with real TIME "
              f"→ cost-based selection would mislead. Gate on real time + S=1, not cost.\n"
              f"(class a=landed, d=no-op/regressão; `≥` = lower bound, original deu timeout.)")
    print("\n".join(table) + "\n" + footer)
    if args.md:
        title = f"# Custo × Tempo — {args.model or 'todos os modelos'} / {args.engine or 'todos os engines'}\n"
        legend = (
            "> **Como ler** — cada linha = uma REESCRITA que rodou (`a` landou · `d` no-op); botchadas (`b`) NÃO entram (sem reescrita válida pra medir).\n"
            ">\n"
            "> - **cost_delta**: mudança de custo (planner/EXPLAIN — **SIMULADO**, não tempo). **NEGATIVO = custo caiu = bom.**\n"
            "> - **time_gain**: ganho de tempo **REAL** (EXPLAIN ANALYZE, warm+mediana). **POSITIVO = mais rápido = bom.** (`≥` = lower bound, original deu timeout.)\n"
            "> - **class**: `a` = landou (S=1 + ganho); `d` = no-op (S=1, sem ganho).\n"
            "> - **verdict**: `agree` = custo e tempo apontam o MESMO lado · `⚠` = **DISCORDAM** (o achado: o custo sozinho enganaria a decisão).\n"
            ">\n"
            "> ⚠️ **Sinais OPOSTOS** (cost_delta↓ é bom, time_gain↑ é bom). **Tese:** gate por **tempo real + S=1, NUNCA por custo**.\n"
            "> Linhas com a MESMA query = runs diferentes dela (estocasticidade).\n"
        )
        with open(args.md, "w") as fh:
            fh.write(title + "\n" + legend + "\n" + "\n".join(table) + "\n" + footer + "\n")
        print(f"\n→ markdown escrito em {args.md}")


if __name__ == "__main__":
    main()
