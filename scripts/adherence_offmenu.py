#!/usr/bin/env python3
"""OFF-MENU probe — did the writer optimise in a way the FIND never asked for?

WHY THIS EXISTS
    The architecture's claim is that the DECISION belongs to the small local model (FIND) and the
    cloud model only WRITES. `adherence_structural.py` answers a weaker question ("did the writer do
    structural work at all?"). This answers the one a reviewer actually attacks: is there any
    transformation in the rewrite that the strategy never named?

    The signature of the failure we are hunting is specific: a structural transformation present in
    the rewrite with NO corresponding item in the strategy. That is the writer deciding on its own.

METHOD
    For every land: parse the ORIGINAL -> REWRITE delta into the structural transformations we can
    detect, and for each one ask whether the strategy text names it. A transformation with no
    matching phrase is OFF MENU. Off-menu cases are then split:
        additive     — a named transformation is ALSO present (the writer did the job, plus extra)
        substitutive — no named transformation is present (the candidate failure case)

HONESTY NOTE — the lexicon was EXPANDED after inspection
    A first pass flagged 19/297 off-menu. All 19 were read by hand. Every one named the observed
    transformation in wording the initial lexicon did not carry: "merge subquery into CTE" and
    "avoid redundant scan" for decorrelation, "common subexpression elimination" for consolidation,
    "subquery inlining" and "materialize intermediate results" for LATERAL, "early aggregation" for
    per-group aggregation. Those phrasings were added below. The expansion is disclosed because it
    was made AFTER seeing the residue: the pre-expansion number (93% on-menu, 0 substitutive after
    manual reading) is reported alongside the post-expansion one, and neither is presented alone.

USAGE
    python scripts/adherence_offmenu.py [--claim-cells | <store> ...]
"""
import glob
import json
import re
import sys
from collections import Counter

sys.path.insert(0, ".")
from scripts.adherence_structural import CLAIM_CELLS, observed_deltas

# For each detectable transformation, the strategy phrasings that legitimately call for it.
# A transformation observed with none of these present in the strategy text is OFF MENU.
EXPLAINS = {
    "cte_introduction":
        r"pre-?filter|pushdown|push down|materiali|\bcte\b|common table|with clause|decorrel|"
        r"consolidat|single (pass|scan)|factor|extract .*subquer|reuse|inlin|"
        r"common subexpression|\bcse\b|derived table|avoid rep|avoid redundant",
    "decorrelation":
        r"decorrel|de-correl|uncorrelat|correlated subquer|materiali|subquer.* to (a )?join|"
        r"rewrite .*subquer|flatten|merge .*subquer|inlin|avoid redundant|avoid rep|"
        r"repeated scan|redundant scan|common subexpression|\bcse\b",
    "consolidation":
        r"consolidat|single (pass|scan)|one pass|conditional aggregat|case when|filter\s*\(where|"
        r"combine|merge .*(scan|aggregat|branch)|unify|common subexpression|\bcse\b|"
        r"early aggregat|avoid rep|avoid redundant|repeated scan",
    "lateral":
        r"lateral|cross apply|correlated|decorrel|inlin|materiali|early aggregat|"
        r"avoid rep|per-?group|common subexpression|\bcse\b",
    "in_subquery_removal":
        r"semi-?join|exists|\bin\b|subquer.* to (a )?join|rewrite .*subquer|flatten|anti-?join|"
        r"materiali|inlin",
    "set_op_removal":
        r"union|set operation|combine|merge|consolidat|single (pass|scan)",
}


def audit(store):
    rows = []
    for path in glob.glob(f".cache/{store}/*.json"):
        try:
            rec = json.load(open(path))
        except Exception:
            continue
        if rec.get("outcome") != "rewrite_correct":
            continue  # only lands reach a user
        original = rec.get("raw_sql") or ""
        rewrite = rec.get("optimized_sql") or rec.get("reached_sql") or ""
        if not original or not rewrite:
            continue
        strategy = (rec.get("strategy") or "").lower()
        deltas = observed_deltas(original, rewrite)
        off = [d for d in deltas if not re.search(EXPLAINS[d], strategy)]
        on = [d for d in deltas if d not in off]
        rows.append({
            "off": off,
            "kind": None if not off else ("additive" if on else "substitutive"),
            "judge": (rec.get("adherence") or {}).get("verdict"),
        })
    return rows


def main():
    args = [a for a in sys.argv[1:] if a != "--claim-cells"]
    stores = args or CLAIM_CELLS
    tot = Counter()
    print(f"{'cell':<40} {'lands':>6} {'on-menu':>9} {'additive':>9} {'substitutive':>13}")
    print("-" * 82)
    for store in stores:
        rows = audit(store)
        if not rows:
            continue
        n = len(rows)
        add = sum(1 for r in rows if r["kind"] == "additive")
        sub = sum(1 for r in rows if r["kind"] == "substitutive")
        tot["n"] += n
        tot["add"] += add
        tot["sub"] += sub
        print(f"{store.replace('suggestions_', ''):<40} {n:>6} {n-add-sub:>9} {add:>9} {sub:>13}")
    print("-" * 82)
    n, add, sub = tot["n"], tot["add"], tot["sub"]
    print(f"{'TOTAL':<40} {n:>6} {n-add-sub:>9} {add:>9} {sub:>13}")
    print(f"\non-menu = {100*(n-add-sub)//max(n,1)}% of accepted rewrites carry ONLY "
          f"transformations the strategy named")
    print(f"substitutive (the failure signature) = {sub}/{n}")


if __name__ == "__main__":
    main()
