#!/usr/bin/env python3
"""Re-measure an index store BY EXECUTION, without spending a single API call.

WHY THIS EXISTS
    The index axis was decided by the planner's COST ESTIMATE in every cell that ran before the
    executed-validation path existed (written 18/08 12:28; the anchor cell finished 11:49). The
    paper has to state, per number, how that number was obtained — so either we declare those as
    estimates, or we re-measure them. Re-measuring is free: the LLM already proposed the indexes and
    they are in the store, so this replays ONLY the physical part (create → execute → roll back).

WHAT IT DOES NOT DO
    It never touches the original store. Results go to a SEPARATE `_revalidated` store, so the
    estimate and the measurement stay side by side and the provenance of both is auditable.

METHODOLOGY — deliberately NOT reimplemented here
    It calls `backend.simulate_index()`, the very function the live pipeline calls, with
    INDEX_VALIDATION=executed. That means warm-up discarded + median of 3, inside a transaction that
    is rolled back. Writing a second timing routine here would risk numbers that are not comparable
    with the ones the pipeline produces. The cost of that choice: the index is rebuilt once per
    (index, query) pair rather than once per index (106 vs 57 builds on the anchor). Worth it.

USAGE
    python scripts/revalidate_indexes.py .cache/index_final_workload_pro [--limit N] [--dry-run]
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
from collections import defaultdict

sys.path.insert(0, "/home/laurapetrola/projects/Athena-2.0")
from dotenv import load_dotenv

load_dotenv("/home/laurapetrola/projects/Athena-2.0/.env", override=False)
# Must be set BEFORE the backend module is imported/called — this is the whole point of the script.
os.environ["INDEX_VALIDATION"] = "executed"

from src.pipeline.backends import get_backend                      # noqa: E402
from src.pipeline.discovery.suggestion_store import _query_hash    # noqa: E402


def load_queries():
    """query_hash[:8] -> (name, sql) for every benchmark query on disk."""
    out = {}
    for f in glob.glob("queries/*/*/*.json"):
        try:
            d = json.load(open(f))
            sql = d.get("sql") or d.get("query") or ""
            if sql:
                out[_query_hash(sql)[:8]] = (os.path.basename(f).replace(".json", ""), sql)
        except Exception:
            pass
    return out


def a_cell_is_running():
    """The campaign owns the database. Measuring index builds against the same instance would both
    contend for I/O and corrupt the timings of the cell in flight — and timings are the result."""
    r = subprocess.run(["pgrep", "-f", "venv/bin/python scripts/run_matrix"],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("store", help="e.g. .cache/index_final_workload_pro")
    ap.add_argument("--limit", type=int, help="measure only the first N pairs (for a smoke test)")
    ap.add_argument("--dry-run", action="store_true", help="list the work, measure nothing")
    a = ap.parse_args()

    out_dir = a.store.rstrip("/") + "_revalidated"
    os.makedirs(out_dir, exist_ok=True)
    queries = load_queries()

    # (table, column, index_type, query_hash) -> the DDL proposed for it
    pairs = {}
    for f in glob.glob(f"{a.store}/*.json"):
        d = json.load(open(f))
        qh = d["query_hash"][:8]
        for r in (d.get("recommendations") or []):
            key = (r.get("table"), r.get("column"), r.get("index_type"), qh)
            pairs.setdefault(key, r)

    done = {tuple(json.load(open(f))["key"]) for f in glob.glob(f"{out_dir}/*.json")}
    todo = [(k, v) for k, v in sorted(pairs.items()) if tuple(k) not in done]
    missing = sorted({k[3] for k, _ in todo if k[3] not in queries})

    print(f"store      : {a.store}")
    print(f"pairs      : {len(pairs)} total · {len(done)} already measured · {len(todo)} to go")
    if missing:
        print(f"⚠️  {len(missing)} query hashes are not on disk — those pairs cannot be measured: {missing}")
    if a.dry_run:
        by_idx = defaultdict(int)
        for k, _ in todo:
            by_idx[k[:3]] += 1
        print(f"distinct indexes: {len(by_idx)}")
        return

    if a_cell_is_running():
        sys.exit("ABORTING: a campaign cell is running. This script builds real indexes on the same "
                 "instance and would corrupt its timings. Run it when the machine is free.")

    backend = get_backend()
    if a.limit:
        todo = todo[:a.limit]

    for i, (key, rec) in enumerate(todo, 1):
        table, column, itype, qh = key
        if qh not in queries:
            continue
        qname, sql = queries[qh]
        spec = {"target": table, "property": column, "index_type": itype,
                "ddl": rec.get("ddl") or f"CREATE INDEX idx_reval_{table}_{column} ON {table} ({column})"}
        t0 = time.time()
        try:
            report = backend.simulate_index(spec, sql, workload=[])
            err = None
        except Exception as e:                                  # a failed build must not kill the batch
            report, err = {}, str(e)[:300]
        out = {
            "key": list(key),
            "query": qname,
            "table": table, "column": column, "index_type": itype,
            "validation": report.get("validation", "failed"),
            "measured_read_benefit_pct": report.get("measured_read_benefit_pct"),
            "baseline_exec_ms": report.get("baseline_exec_ms"),
            "optimized_exec_ms": report.get("optimized_exec_ms"),
            # kept side by side ON PURPOSE: the paper's claim is that the planner OVERESTIMATES, and
            # that claim needs both numbers for the same index, not two separate populations.
            "estimated_read_benefit_pct": report.get("estimated_read_benefit_pct"),
            "estimated_benefit_pct_original": rec.get("estimated_benefit_pct"),
            "recommendation": report.get("recommendation"),
            "error": err,
            "wall_s": round(time.time() - t0, 1),
        }
        fn = f"{table}__{column}__{itype}__{qh}.json".replace("/", "_")
        with open(os.path.join(out_dir, fn), "w") as fh:
            json.dump(out, fh, indent=2)
        m, e = out["measured_read_benefit_pct"], out["estimated_benefit_pct_original"]
        print(f"[{i}/{len(todo)}] {qname:>5} {table}.{column} ({itype}) "
              f"medido={m if m is None else round(m,1)}% estimado={e if e is None else round(e,1)}% "
              f"{out['wall_s']}s" + (f" ERRO: {err}" if err else ""), flush=True)


if __name__ == "__main__":
    main()
