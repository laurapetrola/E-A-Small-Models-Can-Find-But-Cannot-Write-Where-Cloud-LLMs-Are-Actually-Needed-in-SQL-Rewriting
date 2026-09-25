#!/usr/bin/env python3
"""Gera a TABELA-PADRÃO por query de um doc de experimento (reescrita + índice + tempo).

Emite as linhas markdown do bloco "## Resumo por query" — o formato canônico dos docs
(ver documentation/experiments/TEMPLATE_experiment_doc.md). Filosofia: docs REGENERÁVEIS,
nunca números digitados à mão.

Colunas: q | desfecho (3 runs) | melhor gain | inferência (mediana) | reescrita | índice (single) | índice workload
  - desfecho: L=land · ng=no_gain · mf=mechanics_failed · eq=equiv-fail · to=timeout · unv=unverified
  - melhor gain: melhor entre os runs que landaram; `lb`=lower-bound (original estourou → piso conservador)
  - índice (single): a recomendação (tabela.coluna) + benefício simulado NA PRÓPRIA query
  - índice workload: `agg X%` = benefício agregado no workload (21 queries) · `(N regr)` = quantas REGRIDEM (Achado 5)

Uso:
  python scripts/gen_query_table.py <STORE_DIR> [MODEL_SUBSTR] [INDEX_STORE_DIR]
    STORE_DIR      : ex. .cache/suggestions_tpcds_mysql
    MODEL_SUBSTR   : filtro de label (default '' = todos +think sem +noplan). Ex.: '+noplan' p/ M2.
    INDEX_STORE_DIR: default = STORE com 'suggestions'->'index_suggestions'
"""
import json, glob, collections, hashlib, statistics, sys, os

def _h(s): return hashlib.sha256(s.encode()).hexdigest()[:12]

def _query_names():
    nm = {}
    for f in glob.glob("queries/**/*.json", recursive=True):
        d = json.load(open(f))
        if "sql" in d:
            nm[_h(d["sql"])] = f.split("/")[-1][:-5]
    return nm

_SMAP = {"rewrite_correct": "L", "no_gain": "ng", "mechanics_failed": "mf",
         "equivalence_failed_semantic": "eq", "equivalence_failed_structural": "eq",
         "timeout": "to", "unverified": "unv"}

def _canon(txt):
    s = (txt or "").lower()
    for k, v in [("decorrel", "decorrelação"), ("lateral", "JOIN LATERAL"),
                 ("materializ", "CTE/materialize"), ("pre-filter", "pré-filtro CTE"),
                 ("prefilter", "pré-filtro CTE"), ("pré-filtr", "pré-filtro CTE"),
                 ("cte", "CTE"), ("reorder", "reorder joins"), ("window", "window")]:
        if k in s:
            return v
    return "—"

def _rewrite(ds):
    for d in ds:
        for k in ("discovered_techniques", "strategy", "suggestions"):
            v = d.get(k)
            if v:
                return _canon(v if isinstance(v, str) else " ".join(map(str, v)))
    return "—"

def _keep(d, model_substr):
    m = d.get("model") or ""
    # substr EXPLÍCITO → honra (funciona p/ células de ablação: +nothink, +noplan, +schemalink…)
    if model_substr:
        return model_substr in m
    # sem substr → fatia default: reasoning-ON + plano-ON (as células principais)
    if "+think" not in m or "nothink" in m:
        return False
    return "+noplan" not in m

def main(store, model_substr="", idx_store=None):
    idx_store = idx_store or store.replace("suggestions", "index_suggestions")
    nm = _query_names()
    recs = [json.load(open(f)) for f in glob.glob(f"{store}/*.json")]
    recs = [d for d in recs if _keep(d, model_substr)]
    q = collections.defaultdict(list)
    for d in recs:
        q[d["query_hash"]].append(d)
    idx = collections.defaultdict(list)
    if os.path.isdir(idx_store):
        for f in glob.glob(f"{idx_store}/*.json"):
            d = json.load(open(f))
            if _keep(d, model_substr):
                idx[d["query_hash"]].extend(d.get("recommendations") or [])

    def qnum(n):
        try: return int(n[1:])
        except Exception: return 999

    print("| q | desfecho (3) | melhor gain | infer (med) | reescrita | índice (single) | índice workload |")
    print("|---|---|---|---|---|---|---|")
    for qh, ds in sorted(q.items(), key=lambda kv: qnum(nm.get(kv[0], "z999"))):
        n = nm.get(qh, "?")
        seq = [_SMAP.get(d.get("outcome"), d.get("outcome")) for d in ds]
        best, how = None, ""
        for d in ds:
            if d.get("outcome") == "rewrite_correct":
                et = (d.get("metrics") or {}).get("execution_time") or {}
                g, lb = et.get("improvement_pct"), et.get("improvement_lower_bound_pct")
                v = g if g is not None else lb
                if v is not None and (best is None or v > best):
                    best, how = v, ("" if g is not None else "lb ")
        infm = round(statistics.median([(d.get("inference_ms") or 0) / 1000 for d in ds]))
        rw = _rewrite(ds)
        recs_q = idx.get(qh, [])
        if recs_q:
            r = max(recs_q, key=lambda x: (x.get("estimated_benefit_pct") or -999))
            sb, wagg = r.get("estimated_benefit_pct"), r.get("workload_aggregate_benefit_pct")
            regr = sum(1 for x in (r.get("workload_per_query") or []) if (x.get("benefit_pct") or 0) < -0.5)
            idxs = f"{r.get('table')}.{r.get('column')} {sb}%" if sb is not None else "—"
            idxw = f"agg {wagg}% ({regr} regr)" if wagg is not None else "—"
        else:
            idxs = idxw = "—"
        bests = f"{how}{best}%" if best is not None else "—"
        print(f"| {n} | {' · '.join(seq)} | {bests} | {infm}s | {rw} | {idxs} | {idxw} |")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "",
         sys.argv[3] if len(sys.argv) > 3 else None)
