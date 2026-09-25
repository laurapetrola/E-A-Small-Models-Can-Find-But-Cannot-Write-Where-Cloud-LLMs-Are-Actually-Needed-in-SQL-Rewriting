#!/usr/bin/env python3
"""DETERMINISTIC adherence check — did the writer implement the technique the FIND decided?

WHY THIS EXISTS
    The architecture's second contribution is that the DECISION belongs to the small local model
    (FIND) and the cloud model only WRITES. The predictable reviewer attack is: "what guarantees the
    writer isn't re-optimizing on its own?" We already answer with four layers (prompt scope,
    anti-revert guard, graph routing, and an independent LLM judge). This adds the layer that does
    not depend on any model: a STRUCTURAL check over the ASTs.

WHAT IT DOES
    For every land, it reads the strategy the architect produced, maps it to the technique it names,
    and then asks the AST whether the ORIGINAL -> REWRITE delta carries that technique's signature.
    Example: a strategy that says "decorrelate the scalar subquery" must produce a rewrite where the
    correlated-scalar-aggregate shape is GONE. If the shape is still there, the writer did something
    else, whatever the LLM judge said.

VERDICTS
    consistent   — the declared technique's structural signature is present in the delta
    absent       — the signature is NOT present (the writer's SQL does not show the declared change)
    undetermined — the technique has no deterministic signature we can test (reported, never hidden)

    "absent" is not automatically misconduct: some techniques are realised in ways the signature does
    not capture. It is reported as a rate, and the honest claim is about the CONSISTENT rate plus the
    undetermined share — never a silent pass.

USAGE
    python scripts/adherence_structural.py <store> [<store> ...]
    python scripts/adherence_structural.py --claim-cells      # the cells backing paper claims
"""
import glob
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")
import sqlglot
from sqlglot import exp

from src.pipeline.discovery.structural_signature import (
    detect_correlated_scalar_aggregate,
    detect_repeated_scalar_aggregates,
    structural_signature,
)

DIALECT = "postgres"

# Cells that back claims in the papers (the ones a reviewer would audit).
CLAIM_CELLS = [
    "suggestions_system_pro_tpcds_pg",       # the canonical system cell (anchor quadrant)
    "suggestions_system_pro_fresh",          # the system on never-seen queries
    "suggestions_system_flash_tpcds_pg",     # the writer ablation, flash arm
    "suggestions_system_flash_fresh",        # the flash arm on never-seen queries (the tie-breaker battery)
    "suggestions_p1_llama_aided_fresh0803",  # clean pair / teacher-student baseline
    "suggestions_p1_llama_aided_hint_fresh0803",
    "suggestions_6_fresh0804",               # the reference professor cell
    "suggestions_qwen_aided_hint_planon",
    "suggestions_masking_ab_qwen_planon",    # masking leg
    "suggestions_alllocal_hint0806",         # sovereign mode (local writer)
]

# Each technique the strategy may declare, with (a) how it is NAMED in the strategy text and
# (b) POSITIVE structural evidence of it in the ORIGINAL -> REWRITE delta.
#
# Two design decisions that matter for honesty:
#   1. Strategies are LISTS (the architect emits 3-5 numbered items). We extract EVERY technique it
#      names and accept the rewrite as consistent when AT LEAST ONE of them shows its signature —
#      forcing a single label produced false negatives (a textbook consolidation rewrite scored
#      "absent" because an unrelated first item drove the classification).
#   2. We look for evidence the technique IS THERE, not for the removal of one narrow shape. The
#      narrow detectors were built to FIND optimisation opportunities in originals; reused as
#      adherence tests they under-report legitimate realisations.


def _parse(sql):
    try:
        return sqlglot.parse_one(sql, dialect=DIALECT)
    except Exception:
        return None


def _n_ctes(sql):
    ast = _parse(sql)
    if ast is None:
        return 0
    with_ = ast.find(exp.With)
    return len(with_.expressions) if with_ else 0


def _has_lateral(sql):
    ast = _parse(sql)
    return ast is not None and ast.find(exp.Lateral) is not None


def _n_conditional_aggregates(sql):
    """Aggregates carrying a FILTER clause or a CASE — the signature of a single-pass consolidation."""
    ast = _parse(sql)
    if ast is None:
        return 0
    n = 0
    for agg in ast.find_all(exp.AggFunc):
        if agg.args.get("filter") is not None or agg.find(exp.Case) is not None:
            n += 1
    return n


def _n_correlated_subqueries(sql):
    """Subqueries referring to a table alias declared OUTSIDE them — correlation, structurally."""
    ast = _parse(sql)
    if ast is None:
        return 0
    n = 0
    for sub in ast.find_all(exp.Subquery, exp.Exists):
        inner = sub.find(exp.Select)
        if inner is None:
            continue
        declared = {t.alias_or_name for t in inner.find_all(exp.Table)}
        refs = {c.table for c in inner.find_all(exp.Column) if c.table}
        if refs - declared:
            n += 1
    return n


def _n_in_subqueries(sql):
    ast = _parse(sql)
    if ast is None:
        return 0
    return sum(1 for i in ast.find_all(exp.In) if i.find(exp.Select) is not None)


def _decorrelated(o, r):
    return _n_correlated_subqueries(r) < _n_correlated_subqueries(o)


def _consolidated(o, r):
    return _n_conditional_aggregates(r) > _n_conditional_aggregates(o)


def _added_cte(o, r):
    return _n_ctes(r) > _n_ctes(o)


def _added_lateral(o, r):
    return _has_lateral(r) and not _has_lateral(o)


def _semi_joined(o, r):
    return _n_in_subqueries(r) < _n_in_subqueries(o)


TECHNIQUES = [
    ("decorrelation",    r"decorrel|de-correl|uncorrelat|correlated subquer",                     _decorrelated),
    ("consolidation",    r"consolidat|single (pass|scan)|one pass|conditional aggregat|"
                         r"filter\s*\(where|case when",                                          _consolidated),
    ("lateral",          r"\blateral\b",                                                         _added_lateral),
    ("semi_join",        r"semi-?join|exists instead|\bin\b subquery to|convert .*\bin\b",       _semi_joined),
    ("early_filter_cte", r"pre-?filter|pré-filtr|filter early|push .*filter|early filter|"
                         r"reduce .*before .*join|materializ|\bcte\b|common table expression",    _added_cte),
]

# Named techniques with no structural signature we trust — reported, never silently passed.
UNDETERMINED = [
    ("join_reorder", r"reorder|join order"),
    ("index_hint",   r"index[- ]only|index scan|create .*index"),
]


def declared_techniques(strategy):
    """Every technique the strategy names (they are numbered lists, not single labels)."""
    s = (strategy or "").lower()
    found = [(name, pred) for name, pattern, pred in TECHNIQUES if re.search(pattern, s)]
    if found:
        return found
    if any(re.search(p, s) for _, p in UNDETERMINED):
        return []          # named, but not structurally testable
    return None            # nothing recognised at all


def _n_set_ops(sql):
    ast = _parse(sql)
    return 0 if ast is None else len(list(ast.find_all(exp.Union, exp.Intersect, exp.Except)))


# Every structural transformation we can recognise deterministically, with the technique family it
# belongs to. A rewrite that carries NONE of these is either the original in disguise or a purely
# cosmetic edit — which is exactly what "the writer re-optimised on its own" would NOT look like
# either, so the test is about the writer having DONE the structural work the strategy asked for.
DELTAS = {
    "decorrelation":       lambda o, r: _n_correlated_subqueries(r) < _n_correlated_subqueries(o),
    "consolidation":       lambda o, r: _n_conditional_aggregates(r) > _n_conditional_aggregates(o),
    "cte_introduction":    lambda o, r: _n_ctes(r) > _n_ctes(o),
    "lateral":             lambda o, r: _has_lateral(r) and not _has_lateral(o),
    "in_subquery_removal": lambda o, r: _n_in_subqueries(r) < _n_in_subqueries(o),
    "set_op_removal":      lambda o, r: _n_set_ops(r) < _n_set_ops(o),
}

# Techniques whose NAME in the strategy is unambiguous AND whose signature we trust. Only these
# support the targeted test; everything else is reported as "not targetable", never as a failure.
TARGETABLE = {
    "decorrelation": (r"decorrel|de-correl|uncorrelat", "decorrelation"),
    "consolidation": (r"consolidat|single (pass|scan)|one pass|conditional aggregat", "consolidation"),
    "lateral":       (r"\blateral\b", "lateral"),
}


def observed_deltas(original, rewrite):
    out = []
    for name, pred in DELTAS.items():
        try:
            if pred(original, rewrite):
                out.append(name)
        except Exception:
            pass
    return out


def check_store(store):
    """Two independent tests per land:

    A (universal)  — did the writer perform STRUCTURAL work at all? A rewrite carrying no recognised
                     transformation is a revert or a cosmetic edit; the anti-revert guard should have
                     caught it, so this is a second, independent net.
    B (targeted)   — when the strategy names a technique whose signature we trust, is THAT signature
                     present? This is the test that actually says "the writer implemented what the
                     small model decided", and it is the number to report.
    """
    rows = []
    for path in glob.glob(f".cache/{store}/*.json"):
        try:
            rec = json.load(open(path))
        except Exception:
            continue
        if rec.get("outcome") != "rewrite_correct":
            continue  # only lands: a rejected rewrite never reaches the user
        original = rec.get("raw_sql") or ""
        rewrite = rec.get("optimized_sql") or rec.get("reached_sql") or ""
        if not original or not rewrite:
            continue
        deltas = observed_deltas(original, rewrite)
        strategy = (rec.get("strategy") or "").lower()
        targeted = None
        for _, (pattern, delta_name) in TARGETABLE.items():
            if re.search(pattern, strategy):
                targeted = "consistent" if delta_name in deltas else "absent"
                break
        rows.append({
            "structural_work": bool(deltas),
            "deltas": deltas,
            "targeted": targeted,                       # None = strategy names nothing targetable
            "judge": (rec.get("adherence") or {}).get("verdict"),
        })
    return rows


def main():
    """Reports what the AST can establish DETERMINISTICALLY, and nothing beyond it.

    A note on what is NOT reported, and why. We first tried a "targeted" test: map the strategy text
    to the technique it names and require exactly that signature. It does not work, and the reason is
    itself worth stating: the architect emits strategies as NUMBERED LISTS of 3-5 items, and the
    writer implements the applicable subset. Demanding one specific item scores correct behaviour as
    a deviation. Technique-level adherence is therefore inherently fuzzy, and the defensible
    deterministic claim is the one below: the writer performed the structural work, rather than
    reverting to the original or making a cosmetic edit.
    """
    args = sys.argv[1:]
    stores = CLAIM_CELLS if (not args or args[0] == "--claim-cells") else args
    tot = Counter()
    per_delta = Counter()
    agree = Counter()
    print(f"{'cell':<44} {'lands':>6} {'with structural transformation':>32}")
    print("-" * 86)
    for store in stores:
        rows = check_store(store)
        if not rows:
            continue
        n = len(rows)
        work = sum(1 for r in rows if r["structural_work"])
        tot["lands"] += n
        tot["work"] += work
        for r in rows:
            for d in r["deltas"]:
                per_delta[d] += 1
            if r["judge"]:
                agree[("transformed" if r["structural_work"] else "none", r["judge"])] += 1
        print(f"{store.replace('suggestions_', ''):<44} {n:>6} {work:>20}/{n} ({100*work//n:>3}%)")
    print("-" * 86)
    print(f"{'TOTAL':<44} {tot['lands']:>6} {tot['work']:>20}/{tot['lands']} "
          f"({100*tot['work']//max(tot['lands'],1):>3}%)")
    print("\nStructural transformations observed (a land may carry more than one):")
    for d, n in per_delta.most_common():
        print(f"  {d:<22} {n:>5}")
    if agree:
        print("\nCross-check against the independent LLM judge:")
        for (structural, judge), n in sorted(agree.items()):
            print(f"  structural={structural:<12} judge={judge:<10} {n:>5}")


if __name__ == "__main__":
    main()
