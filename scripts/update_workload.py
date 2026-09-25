#!/usr/bin/env python3
"""Regenerate the `workload` array in queries/workload_registry.json from the CURRENT query corpus.

The index simulator (_workload_for_table) reads registry["workload"] = [{source, tables, sql}] to check a
candidate index against EVERY other query that touches the same table. That array was frozen in Phase 3
(7 TPC-DS + 10 JOB); it must reflect the current corpus (curated + held-out + non-curated, TPC-DS + JOB).

This walks queries/{curated,heldout,non-curated}/{TPCDS,JOB}/*.json, extracts the REAL base tables per
query (sqlglot, CTE names excluded), and rewrites ONLY the "workload" key — preserving indexes_created and
_comment. TPC-DS and JOB stay auto-separated by table name (disjoint schemas), so one array is fine.

Usage: python scripts/update_workload.py        (writes the file)
       python scripts/update_workload.py --dry   (preview, no write)
"""
import argparse
import glob
import json
import os

import sqlglot
import sqlglot.expressions as exp

REGISTRY = "queries/workload_registry.json"


def tables_of(sql: str) -> list[str]:
    try:
        ast = sqlglot.parse_one(sql, dialect="postgres")
    except Exception:
        return []
    cte_names = {c.alias_or_name.lower() for c in ast.find_all(exp.CTE)}
    tabs = {t.name.lower() for t in ast.find_all(exp.Table) if t.name}
    return sorted(tabs - cte_names)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    workload = []
    files = []
    for cat in ("curated", "heldout", "non-curated"):   # exclui proof-for-hardware (variantes) + raiz
        files += glob.glob(f"queries/{cat}/**/*.json", recursive=True)
    for f in sorted(files):
        if os.path.basename(f) == "workload_registry.json":
            continue
        try:
            sql = json.load(open(f))["sql"]
        except Exception:
            continue
        rel = os.path.relpath(f, "queries")
        workload.append({"source": rel, "tables": tables_of(sql), "sql": " ".join(sql.split())})

    try:
        registry = json.load(open(REGISTRY))
    except Exception:
        registry = {}
    registry["workload"] = workload

    print(f"{len(workload)} queries no workload:")
    for w in workload:
        print(f"  {w['source']:32} tables={w['tables']}")
    if args.dry:
        print("\n(--dry: nada escrito)")
        return
    with open(REGISTRY, "w") as fh:
        json.dump(registry, fh, indent=2)
    print(f"\n✅ {REGISTRY} atualizado (workload regenerado; indexes_created/_comment preservados).")


if __name__ == "__main__":
    main()
