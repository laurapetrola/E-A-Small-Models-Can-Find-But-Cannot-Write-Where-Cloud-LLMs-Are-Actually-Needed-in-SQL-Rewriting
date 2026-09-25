"""Re-derive the WORKLOAD-AWARE per-query breakdown for index recommendations ALREADY in the store.

WHY: the index simulation is deterministic (hypopg/EXPLAIN cost on static data), so the per-query
workload impact ("index Y: q18 +25%, q40 −10%") can be recomputed offline WITHOUT re-running the LLM
pipeline. Records saved before the workload_per_query fix only kept the aggregate; this backfills the
breakdown so "workload-aware" is DEMONSTRATED (each candidate measured across every workload query
that touches its table, exposing the improve/regress trade-off). All EXPLAIN (cost) — never executes.

Run (same env the server uses — DB_TYPE/DB_URI/WORKLOAD_REGISTRY_PATH):
    python scripts/resimulate_index.py            # print report
    python scripts/resimulate_index.py --md OUT   # also write a markdown table to OUT
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pipeline.backends import get_backend
from src.pipeline.nodes.index_simulation_validator import _workload_for_table

STORE_SUGG = os.getenv("SUGGESTION_STORE_DIR", ".cache/suggestions")  # raw=default; aided seta a env
STORE_INDEX = os.getenv("INDEX_SUGGESTION_STORE_DIR", ".cache/index_suggestions")


def _hash_to_sql() -> dict:
    """query_hash -> raw_sql, from the rewrite store (the index store keeps only the hash)."""
    out = {}
    for f in glob.glob(f"{STORE_SUGG}/*.json"):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get("query_hash") and d.get("raw_sql"):
            out[d["query_hash"]] = d["raw_sql"]
    return out


def _query_name(raw: str) -> str:
    n = re.sub(r"\s+", " ", (raw or "").lower())
    for qf in glob.glob("queries/*/TPCDS/*.json") + glob.glob("queries/*/JOB/*.json"):
        q = json.load(open(qf))
        qs = re.sub(r"\s+", " ", (q.get("query") or q.get("sql") or "").lower())[:80]
        if qs and qs in n:
            return os.path.basename(qf)[:-5]
    return "?"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", help="write a markdown table to this path")
    args = ap.parse_args()

    backend = get_backend()
    h2sql = _hash_to_sql()

    # Dedupe candidates by (query, table, column, type) — the same index recommended across runs
    # only needs simulating once.
    seen: set = set()
    candidates: list[tuple] = []  # (qname, raw_sql, spec)
    for f in glob.glob(f"{STORE_INDEX}/*.json"):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        raw = h2sql.get(d.get("query_hash"))
        if not raw:
            continue
        qn = _query_name(raw)
        for r in d.get("recommendations", []):
            if not isinstance(r, dict) or not r.get("table"):
                continue
            spec = {"target": r["table"], "property": str(r.get("column")), "index_type": r.get("index_type", "btree")}
            key = (qn, spec["target"], spec["property"], spec["index_type"])
            if key in seen:
                continue
            seen.add(key)
            candidates.append((qn, raw, spec))

    rows = []
    for qn, raw, spec in sorted(candidates, key=lambda c: (c[0], c[2]["target"], c[2]["property"])):
        try:
            workload = _workload_for_table(spec["target"], raw)
            rep = backend.simulate_index(spec, raw, workload=workload)
        except Exception as e:
            print(f"  ! {qn} {spec['target']}.{spec['property']}: erro {str(e)[:60]}")
            continue
        wi = rep.get("workload_impact") or {}
        def _stem(s: str) -> str:
            return os.path.basename(s)[:-5] if isinstance(s, str) and s.endswith(".json") else s
        per = [(_stem(q["source"]), q["benefit_pct"]) for q in (wi.get("queries") or []) if q.get("benefit_pct") is not None]
        rows.append({
            "query": qn,
            "index": f"{spec['target']}.{spec['property']} [{spec['index_type']}]",
            "target_benefit_pct": rep.get("estimated_read_benefit_pct"),
            "workload_improved": wi.get("queries_improved"),
            "workload_regressed": wi.get("queries_regressed"),
            "per_query": per,
        })
        per_s = ", ".join(f"{s}:{b:+.1f}%" for s, b in sorted(per, key=lambda x: -(x[1] or 0)))
        print(f"{qn:5} | {rows[-1]['index']:55} | alvo {rep.get('estimated_read_benefit_pct')}% "
              f"| workload +{wi.get('queries_improved')}/−{wi.get('queries_regressed')} | {per_s}")

    if args.md:
        with open(args.md, "w") as f:
            f.write("| query | índice | benefício alvo | workload (improve/regress) | per-query |\n")
            f.write("|---|---|---|---|---|\n")
            for r in rows:
                per_s = ", ".join(f"`{s}` {b:+.1f}%" for s, b in sorted(r["per_query"], key=lambda x: -(x[1] or 0)))
                f.write(f"| {r['query']} | `{r['index']}` | {r['target_benefit_pct']}% "
                        f"| +{r['workload_improved']} / −{r['workload_regressed']} | {per_s or '—'} |\n")
        print(f"\n→ markdown escrito em {args.md}")


if __name__ == "__main__":
    main()
