"""Generate per-query docs (doc_qN.md + doc_index.md) from the campaign records.

Mirrors the hand-written PG docs STRUCTURALLY but is data-driven + reproducible (so every model/engine
gets consistent per-query coverage). Maps each record's raw_sql back to its qN via the query corpus
(queries/**/<BENCH>/qN.json), classifies a/b/c/d from the outcome, pulls the index recommendation
(eixo 2) from .cache/index_suggestions, and tags the ORIGIN of each WRITE failure (engine vs our machinery).

Usage: .venv/bin/python scripts/gen_query_docs.py --engine mysql --benchmark TPCDS [--model qwen-reasoning]
"""
import argparse, glob, hashlib, json, os, re

CLASS_OF = {
    "rewrite_correct": "a",            # FIND+WRITE+gain (landed)
    "no_gain": "d",                    # no-op válido (S=1✓, sem ganho)
    "mechanics_failed": "b",           # botched WRITE
    "equivalence_failed_semantic": "b",
    "equivalence_failed_structural": "b",
    "timeout": "c",                    # never reached
    "no_reach": "c",
}


def norm(sql: str) -> str:
    return re.sub(r"\s+", " ", (sql or "").lower()).strip().rstrip(";")


def fail_origin(err: str) -> str:
    e = (err or "").lower()
    if "pymysql" in e or "operationalerror" in e or "psycopg" in e:
        if "1054" in e or "unknown column" in e: return "MOTOR: Unknown column"
        if "3024" in e or "maximum statement execution" in e: return "MOTOR: timeout da reescrita"
        if "3594" in e: return "MOTOR: restrição de dialeto"
        return "MOTOR: erro de execução"
    if "unknown sources" in e: return "NOSSO: reference guard"
    if e.startswith("syntax error"): return "NOSSO: validate_syntax"
    if "hashes differ" in e or "different number of rows" in e or "integrity check failed" in e:
        return "S=1: equivalência (semântica)"
    if "optimizer produced no output" in e: return "vazio"
    if "duplicate_attempt" in e: return "loop (duplicate)"
    return e[:48] or "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)       # mysql | postgres (db_type filter + path component)
    ap.add_argument("--benchmark", default="TPCDS")
    ap.add_argument("--model", default=None)         # record filter, e.g. qwen-reasoning
    ap.add_argument("--label", default=None)         # path component, e.g. qwen (defaults to --model)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    # Path is MODEL-first: documentation/experiments/<label>/<engine>/<benchmark>
    label = args.label or args.model or "unknown"
    out_dir = args.out or f"documentation/experiments/{label}/{args.engine}/{args.benchmark}"
    os.makedirs(out_dir, exist_ok=True)

    # 1. sql -> qN from the corpus. Dois mapas: norm(sql) (fallback) + query_hash (PRIMÁRIO, robusto).
    #    O raw_sql salvo pode divergir do arquivo mesmo após norm (ex.: q5) → resolver só por norm DROPA
    #    a query do doc apesar de os dados existirem. O query_hash = sha256 do sql do arquivo (igual ao save).
    sql2q, qh2q = {}, {}
    for f in glob.glob(f"queries/**/{args.benchmark}/*.json", recursive=True):
        if os.path.basename(f) == "workload_registry.json":
            continue
        try:
            sql = json.load(open(f))["sql"]
            qn = os.path.splitext(os.path.basename(f))[0]
            sql2q[norm(sql)] = qn
            qh2q[hashlib.sha256(sql.encode()).hexdigest()[:12]] = qn
        except Exception:
            continue

    # 2. index recs by query_hash — guarda TODAS as recs (alvo% E workload% podem vir de índices DIFERENTES:
    #    um índice pode ter alvo 0% mas workload alto = o ponto workload-aware). Filtra por modelo (antes
    #    misturava índices de todos os modelos no doc por-modelo).
    idx_by_qh = {}
    for f in glob.glob(os.path.join(os.getenv("INDEX_SUGGESTION_STORE_DIR", ".cache/index_suggestions"), "*.json")):
        d = json.load(open(f))
        if d.get("db_type") != args.engine: continue
        if args.model and d.get("model") != args.model: continue
        for r in (d.get("recommendations") or []):
            idx_by_qh.setdefault(d.get("query_hash"), []).append({
                "b": r.get("estimated_benefit_pct") or 0.0,
                "wl": r.get("workload_aggregate_benefit_pct") or 0.0,
                "helped": r.get("helped"), "tbl": r.get("table"), "col": r.get("column")})

    # 3. rewrite records grouped by qN
    by_q = {}
    for f in glob.glob(os.path.join(os.getenv("SUGGESTION_STORE_DIR", ".cache/suggestions"), "*.json")):
        d = json.load(open(f))
        if d.get("db_type") != args.engine: continue
        if args.model and d.get("model") != args.model: continue
        qn = qh2q.get(d.get("query_hash")) or sql2q.get(norm(d.get("raw_sql")))
        if not qn: continue
        by_q.setdefault(qn, []).append(d)

    index_rows = []
    for qn in sorted(by_q, key=lambda x: int(re.sub(r"\D", "", x) or 0)):
        recs = by_q[qn]
        runs = []
        for d in recs:
            oc = d.get("outcome"); cls = CLASS_OF.get(oc, "?")
            m = d.get("metrics") or {}; et = m.get("execution_time") or {}
            ip, lb = et.get("improvement_pct"), et.get("improvement_lower_bound_pct")
            gain = (f"{ip:.1f}%" if ip is not None else (f"≥{lb:.1f}%" if lb is not None else "—"))
            hm = (m.get("integrity") or {}).get("hash_match")
            errs = d.get("errors") or []
            origin = fail_origin(errs[0] if errs else "") if cls in ("b", "c") else "—"
            runs.append({"oc": oc, "cls": cls, "gain": gain, "hash": hm, "origin": origin})
        classes = [r["cls"] for r in runs]
        landed = classes.count("a")
        qh = recs[0].get("query_hash")
        idxs = idx_by_qh.get(qh, [])
        best_t = max(idxs, key=lambda x: x["b"], default=None)   # melhor Δcusto na query-ALVO
        best_w = max(idxs, key=lambda x: x["wl"], default=None)  # melhor Δcusto no WORKLOAD (verify)
        tgt = best_t["b"] if best_t else 0.0
        wl = best_w["wl"] if best_w else 0.0
        if not idxs:
            idx_line = "— sem índice recomendado"
        else:
            parts = []
            if best_t and best_t["b"] > 0:
                parts.append(f"**alvo:** `{best_t['tbl']}.{best_t['col']}` benefício **+{best_t['b']:.1f}%** (↓custo)")
            if best_w and best_w["wl"] > 0:
                parts.append(f"**workload:** `{best_w['tbl']}.{best_w['col']}` benefício **+{best_w['wl']:.1f}%** (↓custo)")
            idx_line = " · ".join(parts) if parts else "recomendou, mas benefício ~0% (alvo e workload)"
        # per-query doc
        lines = [f"# {qn} ({args.benchmark}, {args.engine}) — unaided\n",
                 f"**reach:** {len(runs)}/{len(runs)} · **land (S=1):** {landed}/{len(runs)} · "
                 f"**classes:** {dict((c, classes.count(c)) for c in sorted(set(classes)))}\n",
                 "## Runs", "| run | outcome | classe | ganho | hash | origem da falha |",
                 "|---|---|---|---|---|---|"]
        for i, r in enumerate(runs, 1):
            lines.append(f"| {i} | {r['oc']} | {r['cls']} | {r['gain']} | {r['hash']} | {r['origin']} |")
        lines += ["", "## Eixo 2 — índice", f"- {idx_line}", "",
                  "> Índice = benefício de **custo via EXPLAIN** (hypopg p/ btree/hash/brin; **CREATE→EXPLAIN→ROLLBACK "
                  "real** p/ GIN/gist, que o hypopg não suporta — índice criado e descartado) — **custo, NÃO tempo** "
                  "(custo≠tempo; **positivo = redução de custo = melhoria**). **alvo** = benefício na query-alvo; "
                  "**workload** = benefício agregado no workload (verify workload-aware — pode ser alto mesmo com alvo 0%). "
                  "Flag, não decisão; tempo real só em spot-check."]
        open(f"{out_dir}/doc_{qn}.md", "w").write("\n".join(lines) + "\n")
        index_rows.append((qn, len(runs), landed, dict((c, classes.count(c)) for c in sorted(set(classes))), tgt, wl))

    # doc_index.md
    idx = [f"# Índice por query — {args.benchmark} / {args.engine}"
           f"{(' / ' + args.model) if args.model else ''} (unaided)\n",
           "| query | reach | land | classes | benefício custo ALVO | benefício custo WORKLOAD |",
           "|---|---|---|---|---|---|"]
    tot_land = sum(r[2] for r in index_rows); tot_runs = sum(r[1] for r in index_rows)
    for qn, n, land, cls, tgt, wl in index_rows:
        idx.append(f"| [{qn}](doc_{qn}.md) | {n}/{n} | {land}/{n} | {cls} | {tgt:+.1f}% | {wl:+.1f}% |")
    idx.append(f"\n**Total:** land {tot_land}/{tot_runs} · {len(index_rows)} queries. "
               f"benefício = **redução de custo** SIMULADA (hypopg; **positivo = melhoria**; custo≠tempo) — **ALVO** = query-alvo, **WORKLOAD** = verify "
               f"workload-aware (índice pode ter alvo 0% e workload alto). Gerado por `scripts/gen_query_docs.py`.")
    open(f"{out_dir}/doc_index.md", "w").write("\n".join(idx) + "\n")
    print(f"gerados {len(index_rows)} doc_qN.md + doc_index.md em {out_dir}")


if __name__ == "__main__":
    main()
