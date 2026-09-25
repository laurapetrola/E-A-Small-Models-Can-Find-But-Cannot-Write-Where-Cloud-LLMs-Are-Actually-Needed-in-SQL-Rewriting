#!/usr/bin/env python
"""Mapa de estratégias de FIND — que técnicas o architect DESCOBRE, e qual o desfecho de cada uma.

Caracteriza o lado FIND (complemento do WRITE): parseia as técnicas do campo `strategy` (2-agentes) ou
`suggestions` (raw), NORMALIZA nomes ruidosos em técnicas CANÔNICAS (keyword-based, transparente — sem
embedding), e cruza TÉCNICA × OUTCOME. Roda sobre qualquer store (env SUGGESTION_STORE_DIR / --dir).

Uso:
  python scripts/find_strategy_map.py --dir archive/.../suggestions        # aided (strategy)
  python scripts/find_strategy_map.py --dir .cache/suggestions --raw       # raw (suggestions)
"""
import argparse, glob, json, os, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # raiz do repo p/ `import src`

# Técnicas CANÔNICAS (keyword → bucket). Ordem importa: a 1ª que casar vence.
CANON = [
    ("decorrelação",        ("correlat", "decorrelat", "scalar subquery", "scalar aggregate")),
    ("semi-join (IN/EXISTS)",("semi-join", "semi join", "in subquery", "in-subquery", "exists", "in list", "in clause")),
    ("materializar/CTE",    ("materializ", "cte", "precompute", "precomput", "consolidat", "derived table", "common table")),
    ("reordenar joins",     ("reorder join", "join order", "join reorder", "optimize join")),
    ("filtro cedo/pushdown",("filter early", "pushdown", "push down", "push the date", "push filter", "pre-filter", "prefilter", "limit early", "filter condition", "move filter", "filter pushdown", "reduce data")),
    ("índice/DDL",          ("index", "índice", "b-tree", "btree")),
    ("agregação/group by",  ("hash aggregat", "group by", "rollup", "grouping set", "aggregat")),
    ("set-op (union/intersect)", ("union", "intersect", "except")),
    ("window",              ("window",)),
    ("dividir OR/subq",     ("split an or", "split or", "split the", "split window", "split aggregate", "or predicate")),
    ("avoid full scan",     ("full table scan", "full scan", "avoid scan", "sequential scan")),
    # ⚠️ "hints de planner/engine" — NÃO são reescritas SQL (o rewrite não escolhe join algo/paralelismo/sort);
    # o modelo confunde "otimizar SQL" com "tunar o executor". Bucket separado = achado de qualidade do FIND.
    ("hint de planner (não-rewrite)", ("hash join", "nested loop", "merge join", "parallel", "bitmap", "sort", "work mem", "work_mem", "partition", "anti join", "seq scan")),
    ("outros joins/subq",   ("join", "subquer", "nested", "redundant")),
]

def canon(t: str) -> str:
    tl = t.lower()
    for name, kws in CANON:
        if any(k in tl for k in kws):
            return name
    return f"outro: {t[:34]}"

def techs_from_strategy(strat: str):
    out = []
    for ln in (strat or "").splitlines():
        m = re.match(r"\s*\d+\.\s*\**([^—\-*]+?)\**\s*[—\-]", ln)  # "N. **TECH** — ..."
        if m:
            out.append(m.group(1).strip())
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.getenv("SUGGESTION_STORE_DIR", ".cache/suggestions_aided"))
    ap.add_argument("--raw", action="store_true", help="fonte = campo `suggestions` (raw) em vez de `strategy` (2-agentes)")
    ap.add_argument("--model", default=None, help="filtra por label de modelo (ex.: qwen-reasoning)")
    ap.add_argument("--vs", default=None, help="2º store p/ COMPARAR técnicas de FIND por query (ex.: mistral × qwen)")
    ap.add_argument("--label1", default="A"); ap.add_argument("--label2", default="B")
    args = ap.parse_args()

    if args.vs:  # modo COMPARAÇÃO: técnicas canônicas por query, dois stores lado a lado
        from src.pipeline.discovery.suggestion_store import _query_hash
        qmap = {}
        for f in glob.glob("queries/**/*.json", recursive=True):
            try:
                q = json.load(open(f)); s = q.get("sql") or q.get("query") or ""
                if isinstance(s, str) and s.strip(): qmap[_query_hash(s)[:8]] = os.path.basename(f).replace(".json", "")
            except Exception: pass
        def techset(store):
            by = defaultdict(set)
            for f in glob.glob(os.path.join(store, "*.json")):
                d = json.load(open(f)); q = qmap.get(d.get("query_hash", "")[:8])
                rt = (d.get("suggestions") or []) if args.raw else techs_from_strategy(d.get("strategy"))
                for t in rt: by[q].add(canon(t))
            return by
        a, b = techset(args.dir), techset(args.vs)
        print(f"# Diff de FIND por query — {args.label1} ({args.dir}) × {args.label2} ({args.vs})\n")
        for q in sorted(set(a) | set(b), key=lambda x: (x is None, x)):
            if q is None: continue
            sa, sb = a.get(q, set()), b.get(q, set())
            comum = sorted(sa & sb); so_a = sorted(sa - sb); so_b = sorted(sb - sa)
            print(f"{q}: comum={comum or '—'}")
            if so_a: print(f"     só {args.label1}: {so_a}")
            if so_b: print(f"     só {args.label2}: {so_b}")
        return

    freq = Counter()
    by_out = defaultdict(Counter)   # técnica canônica → Counter(outcome)
    lands = defaultdict(list)       # técnica → [impr% quando landou]
    n = 0
    for f in sorted(glob.glob(os.path.join(args.dir, "*.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if args.model and (d.get("model") or "") != args.model:
            continue
        raw_techs = (d.get("suggestions") or []) if args.raw else techs_from_strategy(d.get("strategy"))
        if not raw_techs:
            continue
        n += 1
        oc = d.get("outcome")
        seen = set(canon(t) for t in raw_techs)
        for c in seen:
            freq[c] += 1
            by_out[c][oc] += 1
            if oc == "rewrite_correct" and d.get("improvement_pct") is not None:
                lands[c].append(d["improvement_pct"])

    print(f"# Mapa de FIND — {args.dir} ({'raw/suggestions' if args.raw else 'aided/strategy'}) — {n} runs\n")
    print(f"{'técnica (canônica)':30s} {'runs':>4s} {'land':>5s} {'no_gain':>7s} {'mech':>5s} {'S≠1':>4s} {'unver':>6s}  land%médio")
    for c, cnt in freq.most_common():
        o = by_out[c]
        land = o.get("rewrite_correct", 0)
        lp = f"{sum(lands[c])/len(lands[c]):.0f}%" if lands[c] else "—"
        print(f"{c:30s} {cnt:4d} {land:5d} {o.get('no_gain',0):7d} {o.get('mechanics_failed',0):5d} "
              f"{o.get('equivalence_failed_semantic',0):4d} {o.get('unverified',0):6d}  {lp}")

if __name__ == "__main__":
    main()
