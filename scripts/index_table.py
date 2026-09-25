#!/usr/bin/env python3
"""Index-recommendation review table (the list to CREATE MANUALLY + test EXPLAIN ANALYZE before/after).

Reads .cache/index_suggestions/ (saved by save_index_run during the matrix — EXPLORE mode) and aggregates
per (engine × table × column × index_type): how often it was recommended, by which models, the mean
SIMULATED read benefit, and the workload aggregate benefit. The system only SIMULATES (hypopg cost);
this gives you the shortlist to create for real and measure actual EXPLAIN ANALYZE time before/after.

Usage: python scripts/index_table.py [--csv]
"""
import argparse
import glob
import json
import os
from collections import defaultdict

STORE = os.getenv("INDEX_SUGGESTION_STORE_DIR", ".cache/index_suggestions")  # raw=default; aided seta a env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="store_true")
    args = ap.parse_args()

    cells = defaultdict(lambda: {"n": 0, "models": set(), "queries": set(), "benefits": [], "wl": [], "helped": 0})
    for f in glob.glob(f"{STORE}/*.json"):
        try:
            r = json.load(open(f))
        except Exception:
            continue
        engine = r.get("db_type") or "?"
        model = r.get("model") or "?"
        qh = r.get("query_hash") or "?"
        for rec in r.get("recommendations", []):
            key = (engine, rec.get("table"), rec.get("column"), rec.get("index_type"))
            c = cells[key]
            c["n"] += 1
            c["models"].add(model)
            c["queries"].add(qh)
            if rec.get("estimated_benefit_pct") is not None:
                c["benefits"].append(rec["estimated_benefit_pct"])
            if rec.get("workload_aggregate_benefit_pct") is not None:
                c["wl"].append(rec["workload_aggregate_benefit_pct"])
            if rec.get("helped"):
                c["helped"] += 1

    rows = []
    for (engine, table, col, itype), c in cells.items():
        b = f"{sum(c['benefits'])/len(c['benefits']):.1f}%" if c["benefits"] else "—"
        wl = f"{sum(c['wl'])/len(c['wl']):.1f}%" if c["wl"] else "—"
        ddl = f"CREATE INDEX idx_{table}_{col}_{(itype or '').split()[0]} ON {table} ({col});"
        rows.append([engine, table, col, itype, c["n"], len(c["models"]), len(c["queries"]),
                     f"{c['helped']}/{c['n']}", b, wl, ddl])
    # sort by mean simulated benefit (desc), '—' last
    rows.sort(key=lambda r: -(float(r[8][:-1]) if r[8] != "—" else -1))

    header = ["engine", "table", "column", "index_type", "rec_x", "models", "queries",
              "helped", "sim_benefit", "workload_benefit", "DDL (create to test for real)"]
    if args.csv:
        print(",".join(header))
        for row in rows:
            print(",".join(f'"{x}"' for x in row))
        return
    if not rows:
        print("(no index recommendations in the store — run the matrix, the advisor fires when there's no good rewrite)")
        return
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(str(x) for x in row) + " |")
    print("\nsim_benefit = SIMULATED read benefit (hypopg, cost) — CREATE the index and measure real EXPLAIN ANALYZE "
          "time (before→after) to confirm. helped = how many times it simulated > 1%.")


if __name__ == "__main__":
    main()
