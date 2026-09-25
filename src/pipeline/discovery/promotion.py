"""Discovery promotion — AGNOSTIC orchestration of the pre-registered policy.

See documentation/methodology/metodologia.md (§3). The promotion criteria and the orchestration are
universal (SQL / graph / document). Promotion no longer writes architecture B (a transform) by
itself: it REPORTS the clusters that pass the criteria as CANDIDATES for a human to formalize by
hand-writing a deterministic detector. Auto-synthesis of transform code was removed (it could not
synthesize a correct A transform — experiments_details §5.4); detectors are hand-seeded now.

Lifecycle: free-form discovery captures what the LLM proposes (Fix A) -> clusters that pass the
criteria here surface as candidates -> a human hand-writes the deterministic detector that routes
the matching shape into the decomposed (detector-only) path.
"""

import json
import re
from dataclasses import dataclass, field

# Held-out validation set (preregistration §0): queries OUTSIDE the training set and the workload
# registry, used ONLY to check that a promoted transform generalizes to UNSEEN queries/tables.
# Spans BOTH benchmarks so any discovered heuristic has a held-out of its own shape, and so
# JOB<->TPC-DS cross-benchmark validation (the strongest agnosticism evidence) is possible:
#   TPC-DS: q3 (star-join), q11 (UNION/CSE), q30/q81 (decorrelation, different tables than q1)
#   JOB:    2a/10a/16a (flat joins, 5/7/8 tables, families NOT in training) — validate flat-join
#           heuristics (compound_prefilter / subquery_filters / join reorder).
DEFAULT_HELD_OUT_PATHS = [
    "queries/heldout/tpcds-query3.json",
    "queries/heldout/tpcds-query11.json",
    "queries/heldout/tpcds-query30.json",
    "queries/heldout/tpcds-query81.json",
    "queries/heldout/2a.json",
    "queries/heldout/10a.json",
    "queries/heldout/16a.json",
]

# Pre-registered thresholds — FROZEN 2026-06-15 (preregistration.md §A.3). Do NOT tune mid-campaign.
MIN_FREQUENCY = 4            # (a) technique appears in >= 4 distinct runs
MIN_MODELS = 2              # (b) discovered independently by >= 2 of the 3 matrix models
MIN_LANDED_GAIN_PCT = 1.0   # (c) real-time gain above the noise floor on runs where it LANDED
# Single-model tier (added 2026-06-18): the anti-hardcoding defense (the reject reason of the prior
# work was that the RESEARCHER hardcoded the techniques) is met as soon as the LLM — not the researcher —
# DISCOVERS the technique; that holds even with ONE model. Cross-model (b) is a SEPARATE, stronger claim
# (objective convergence), not the hardcoding defense. So a technique found RELIABLY by a single model
# (high per-model frequency) + landing real gain is promoted too, but LABELED tier="single_model" (vs
# "convergent"). Higher reliability bar to compensate for the missing 2nd model (≈ found in all runs of
# one model, e.g. 3/3). Nothing is lost: the worked before→after pair is stored regardless.
MIN_FREQUENCY_SINGLE_MODEL = 3


@dataclass
class CriteriaResult:
    passed: bool
    tier: str | None = None   # "convergent" (≥2 models) | "single_model" (1 model, reliable) | None
    reasons: dict = field(default_factory=dict)


def evaluate_discovery_criteria(
    cluster: dict,
    *,
    min_frequency: int = MIN_FREQUENCY,
    min_models: int = MIN_MODELS,
    min_gain_pct: float = MIN_LANDED_GAIN_PCT,
    min_frequency_single: int = MIN_FREQUENCY_SINGLE_MODEL,
) -> CriteriaResult:
    """Two-tier promotion gate, computable from the discovery store BEFORE paying to generate +
    held-out-validate the transform (criteria d, e, which need an LLM + query execution).

      Tier "convergent"   — (a) freq >= min_frequency AND (b) >= min_models distinct models AND (c) gain.
                            The strongest claim: independent models CONVERGED (objective importance).
      Tier "single_model" — exactly 1 model, but found RELIABLY (freq >= min_frequency_single) AND (c) gain.
                            Defends the anti-hardcoding claim (the MODEL discovered it, not the researcher)
                            without the cross-model convergence strength — promoted + LABELED, not hidden.

      (a) frequency   — appears in >= min_frequency distinct runs
      (b) cross-model — reached by >= min_models distinct models (a strong model landing it and a weak
                        model botching it both count: the weak one is what Pass 2 rescues)
      (c) gain        — mean improvement on rewrite_correct runs >= min_gain_pct (above noise floor)
    """
    freq = cluster.get("frequency", 0)
    models = cluster.get("model_count", 0)
    gain_ok = cluster.get("landed_avg_improvement", 0.0) >= min_gain_pct
    convergent = freq >= min_frequency and models >= min_models and gain_ok
    single = (not convergent) and models <= 1 and freq >= min_frequency_single and gain_ok
    tier = "convergent" if convergent else ("single_model" if single else None)
    reasons = {
        "frequency": freq >= min_frequency,
        "cross_model": models >= min_models,
        "gain": gain_ok,
        "single_model_reliable": models <= 1 and freq >= min_frequency_single,
    }
    return CriteriaResult(passed=tier is not None, tier=tier, reasons=reasons)


# --- Hint-B (degrau 1) gate — DISTINCT from the detector gate above ----------------------------------
# The DETECTOR (hardcoded, production) keeps the strict requirements of evaluate_discovery_criteria
# (convergent / single_model tier + gain + the human's held-out + equivalence-oracle validation), because
# it is permanent production code applied to every matching query with NO LLM. The hint-B is only an
# OFFLINE teaching aid for the guided experiment (never production, still S=1-gated), so it earns a LOWER,
# teaching-shaped bar: someone CAN do it (a landing = worked example + proven gain) and someone else WANTS
# it but can't (a genuine reach that botched the WRITE). That is exactly "shows it works, but they don't
# know how to do it" — and it does NOT require the cross-model CONVERGENCE strength the detector needs.

def _norm_sql(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip().rstrip(";")


def _genuine_reach(rec: dict, backend) -> bool:
    """A CREDIBLE reach (not a hallucinated label): the model's reached attempt PARSES and is a real
    RESTRUCTURE of the original — not garbage that fails syntax, nor the original merely reformatted. This
    filters weak-model label noise (e.g. llama labelling a syntactically broken attempt 'consolidation')."""
    sql = (rec.get("reached_sql") or "").strip()
    raw = (rec.get("raw_sql") or "").strip()
    if not sql:
        return False                              # no reached attempt stored → can't credit a reach
    try:
        valid, _ = backend.validate_syntax(sql)
    except Exception:
        valid = False
    if not valid:
        return False                              # doesn't parse → hallucinated label on broken SQL
    return _norm_sql(sql) != _norm_sql(raw)       # must be a real restructure, not the original reformatted


@dataclass
class TeachableResult:
    teachable: bool
    landed_models: list = field(default_factory=list)
    student_models: list = field(default_factory=list)   # genuine reach, never landed = the ones to teach
    reasons: dict = field(default_factory=dict)


def evaluate_teachable(cluster: dict, backend) -> TeachableResult:
    """Hint-B (degrau 1) gate. Teaching a technique is worth it when BOTH hold:
      (1) >= 1 model LANDED it (outcome=rewrite_correct) — proves the gain EXISTS and gives the worked
          before→after the teacher writes the hint from;
      (2) >= 1 OTHER model GENUINELY reached it but did NOT land — a model that WANTS the technique but
          can't write it = the student the hint targets.
    A genuine reach is SQL-evidenced (_genuine_reach), so a weak model's HALLUCINATED label on broken SQL
    does NOT count — the llama-noise that inflates the detector's cross-model count is excluded here.
    Needs the cluster's `reach_records` (added by discover_clusters)."""
    recs = cluster.get("reach_records") or []
    landed = sorted({r["model"] for r in recs if r.get("outcome") == "rewrite_correct" and r.get("model")})
    genuine = {r["model"] for r in recs if r.get("model") and _genuine_reach(r, backend)}
    students = sorted(m for m in genuine if m not in landed)   # genuine reach, never landed → needs teaching
    teachable = bool(landed) and bool(students)
    return TeachableResult(
        teachable=teachable, landed_models=landed, student_models=students,
        reasons={"has_landing": bool(landed), "has_genuine_student": bool(students)},
    )


def promote_heuristics(backend, clusters: list[dict], held_out_queries: list[dict]) -> list[dict]:
    """Run the pre-registered criteria over discovered clusters and REPORT candidates for a human.

    Promotion no longer auto-formalizes anything. For each cluster it evaluates the (a)(b)(c) gate
    and emits a report entry whose `status` is:

      * "rejected_abc"                     -> the cluster did not pass (a)(b)(c)
      * "skipped_superseded_by_seeded_a"   -> it passed, but an example matches a shape already covered
                                              by a HAND-SEEDED A detector (re-formalizing it would only
                                              re-introduce the prompt bias we removed)
      * "candidate"                        -> it passed and is NOT a seeded-A shape; a human formalizes it
                                              by hand-writing a deterministic detector

    `promoted` is always False — there is no auto-promotion. No transform code is generated, no rule is
    injected into learned_rules; that work is now done by hand. Returns the per-cluster report.
    """
    report: list[dict] = []
    for c in clusters:
        crit = evaluate_discovery_criteria(c)
        entry = {
            "representative": c.get("representative"),
            "criteria_abc": crit.reasons,
            "tier": crit.tier,   # "convergent" | "single_model" | None — the STRENGTH label of the candidate
            "status": "rejected_abc",
            "promoted": False,
        }
        if not crit.passed:
            report.append(entry)
            continue

        # GUARD — don't surface a discovery already formalized as a HAND-SEEDED A detector. Keys on the
        # SAME structural recognizer A uses, over the cluster's worked examples — so the same discovery
        # from ANY engine (PG/MySQL share the decorrelation shape) is caught ONCE, not re-reported per engine.
        examples = c.get("sql_examples") or []
        if any(backend.is_seeded_a_shape((ex or {}).get("before") or "") for ex in examples):
            entry["status"] = "skipped_superseded_by_seeded_a"
            report.append(entry)
            continue

        # Passed (a)(b)(c) and is not a seeded-A shape -> a human formalizes it by hand-writing a detector.
        entry["status"] = "candidate"
        report.append(entry)
    return report


def load_held_out(paths: list[str]) -> list[dict]:
    """Load held-out query files ({"sql": ...}); missing/invalid files are skipped (fail-open)."""
    out: list[dict] = []
    for p in paths:
        try:
            with open(p) as f:
                out.append(json.load(f))
        except Exception:
            pass
    return out


def run_promotion(backend, held_out_paths: list[str] | None = None, discover=None) -> list[dict]:
    """Entry point — the batch the campaign runs at the end of the discovery phase. AGNOSTIC:
      1. discover clusters from the suggestion store (discover, default = relational discover_clusters)
      2. report those passing the policy as candidates a human formalizes by hand-writing a detector.
    Returns the per-cluster report. Runs only after the discovery phase, never per-run
    (preserves train/freeze reproducibility)."""
    if discover is None:
        from src.pipeline.backends.relational.heuristic_discoverer import discover_clusters
        discover = discover_clusters
    held_out = load_held_out(held_out_paths or DEFAULT_HELD_OUT_PATHS)
    clusters = discover()
    return promote_heuristics(backend, clusters, held_out)
