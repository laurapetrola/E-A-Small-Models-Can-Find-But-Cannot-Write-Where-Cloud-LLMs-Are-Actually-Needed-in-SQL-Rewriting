#!/usr/bin/env python3
"""THE characterization table (the paper's core artifact — see novas_direcoes_paper.md §3).

Reads the discovery store (.cache/suggestions/*.json) and emits, per (model × engine × condition),
the FIND/WRITE breakdown:

  reach (FIND)  — the run proposed a real optimization (genuine reach: parseable restructure, not a
                  hallucinated label on broken/unchanged SQL)
  land  (WRITE) — produced VALID + EQUIVALENT SQL (outcome=rewrite_correct, S=1 passed)
  false-pos     — proposed a rewrite the S=1 gate REJECTED as non-equivalent (the "fast-but-wrong" catch)
  gain          — mean real-time improvement on landed runs
  classes       — a (find+write) / b (find, botched write) / c (no reach)

Pure observability: reads the store, derives metrics, prints. No DB, no LLM. Run anytime.
Usage: python scripts/characterization_table.py [--csv]
"""
import argparse, glob, json, os, re
from collections import defaultdict

STORE = os.getenv("SUGGESTION_STORE_DIR", ".cache/suggestions")  # raw=default; aided seta a env


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip().rstrip(";")


def _genuine_reach(rec: dict) -> bool:
    """A credible reach: the reached attempt exists and is a real RESTRUCTURE of the original (not the
    original reformatted). (Syntax-level validity needs the backend; here we use the structural diff,
    which already filters 'returned the original' and empty.)"""
    sql = (rec.get("reached_sql") or rec.get("optimized_sql") or "").strip()
    raw = (rec.get("raw_sql") or "").strip()
    return bool(sql) and _norm(sql) != _norm(raw)


def classify(rec: dict) -> str:
    """FIND × WRITE quadrante: a = FIND+WRITE+ganho (landed) · b = FIND mas WRITE botchado (mechanics
    ou não-equivalente) · c = no reach · d = no-op válido (reach + S=1 ✓ mas sem ganho — planner já
    achata, ou regride). NB: no_gain ⟹ o gate aprovou (S=1 passou), só faltou ganho → é 'd', não 'b'."""
    o = rec.get("outcome") or ""
    if o == "rewrite_correct":
        return "a"
    if o == "mechanics_failed" or o.startswith("equivalence_failed"):
        return "b"
    # no_gain = reescreveu equivalente (S=1 ✓) mas sem ganho/regrediu → no-op VÁLIDO = classe d.
    # (Distinto de b, que é WRITE quebrado. Só conta como reach genuíno; senão é c.)
    return "d" if _genuine_reach(rec) and o == "no_gain" else "c"


def is_false_positive(rec: dict) -> bool:
    """Model proposed a rewrite the S=1 gate rejected as NON-EQUIVALENT — the safety catch."""
    return (rec.get("outcome") or "").startswith("equivalence_failed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="store_true", help="emit CSV instead of a markdown table")
    args = ap.parse_args()

    cells = defaultdict(lambda: {"n": 0, "reach": 0, "land": 0, "fp": 0, "gains": [], "inf": [], "a": 0, "b": 0, "c": 0, "d": 0, "to": 0, "ts": []})
    for f in glob.glob(f"{STORE}/*.json"):
        try:
            r = json.load(open(f))
            r = r[0] if isinstance(r, list) else r
        except Exception:
            continue
        model = r.get("model") or "?"
        engine = r.get("db_type") or "?"
        cond = "guided" if r.get("apply_mode") else "unaided"
        cls = classify(r)
        c = cells[(model, engine, cond)]
        c["n"] += 1
        if r.get("timestamp"):
            c["ts"].append(r["timestamp"][:10])   # DATA REAL do run (staleness à primeira vista — convenção 2026-07-13)
        if _genuine_reach(r):
            c["reach"] += 1
        if r.get("outcome") == "rewrite_correct":
            c["land"] += 1
            if r.get("improvement_pct") is not None:
                c["gains"].append(r["improvement_pct"])
        if is_false_positive(r):
            c["fp"] += 1
        if r.get("inference_ms") is not None:
            c["inf"].append(r["inference_ms"])   # TOTAL LLM time/run (overhead metric — ~90% of pipeline)
        if r.get("outcome") == "timeout":
            c["to"] += 1                          # timeouts (a sub-kind of class-c: model ran away, no output)
        c[cls] += 1

    rows = []
    for (model, engine, cond), c in sorted(cells.items()):
        gain = f"{sum(c['gains'])/len(c['gains']):.1f}%" if c["gains"] else "—"
        inf = f"{sum(c['inf'])/len(c['inf'])/1000:.0f}s" if c["inf"] else "—"   # mean LLM inference/run
        date = (min(c["ts"]) if min(c["ts"]) == max(c["ts"]) else f"{min(c['ts'])}–{max(c['ts'])}") if c["ts"] else "?"
        rows.append([model, engine, cond, date, c["n"], f"{c['reach']}/{c['n']}", f"{c['land']}/{c['n']}",
                     c["fp"], gain, inf, c["a"], c["b"], c["d"], c["c"], c["to"]])

    header = ["model", "engine", "cond", "date", "n", "reach", "land(S=1)", "false-pos", "gain", "inf(avg)", "a", "b", "d", "c", "timeout"]
    if args.csv:
        print(",".join(header))
        for row in rows:
            print(",".join(str(x) for x in row))
        return
    if not rows:
        print("(store empty — run the matrix first)")
        return
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(str(x) for x in row) + " |")
    print("\nclasses: a=FIND+WRITE+ganho (landed) · b=FIND but botched WRITE · d=no-op válido (S=1✓, sem ganho) "
          "· c=did not reach. false-pos = rewrite the S=1 gate rejected (fast-but-wrong).")


if __name__ == "__main__":
    main()
