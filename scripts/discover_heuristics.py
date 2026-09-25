#!/usr/bin/env python3
"""
Discovery candidate REPORT (+ optional teaching-hint writer).

Clusters the stored discovery suggestions and, for each pattern, shows how often it was reached,
by how many models, the landed time gain, and the VERDICT against the promotion criteria
(a: frequency, b: cross-model, c: gain). REPORT-ONLY by default — it does NOT generate or apply any
production code. A pattern marked CANDIDATE is formalized by a HUMAN, who hand-writes a deterministic
detector (recognize + build).

With --write-hints, the TEACHER model (qwen, the discoverer) writes + injects a natural-language
teaching HINT (learned_rules) for each CANDIDATE — the rung-1 aid that guides a WEAKER model in
free-form discovery (APPLY_HEURISTICS=on). This is the MANUAL trigger used during the testing phase
(run it AFTER all unaided baselines are collected, so the hint never contaminates a Pilar-1 run);
in deployment it would be a scheduled offline batch.

Usage:
  python scripts/discover_heuristics.py [--min-frequency N] [--top N] [--write-hints]
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pipeline.backends import get_backend
from src.pipeline.backends.relational.heuristic_discoverer import discover_clusters, write_and_inject_hint
from src.pipeline.discovery.promotion import evaluate_discovery_criteria, evaluate_teachable


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "heuristic"


def _verdict(cluster: dict, backend) -> tuple[str, bool]:
    """Returns (human-readable verdict, is_candidate). is_candidate = passes a/b/c AND not already a detector."""
    crit = evaluate_discovery_criteria(cluster)
    examples = cluster.get("sql_examples") or []
    seeded = crit.passed and any(backend.is_seeded_a_shape((e or {}).get("before") or "") for e in examples)
    if seeded:
        return "ALREADY A DETECTOR (superseded — do not re-formalize)", False
    if crit.passed:
        return f"✅ CANDIDATE → formalize (hand-written detector) [tier={crit.tier}]", True
    missing = [k for k in ("frequency", "cross_model", "gain") if not crit.reasons.get(k)]
    return f"— not yet (missing: {', '.join(missing)})", False


def _print_cluster(cluster: dict, verdict: str) -> None:
    models = ", ".join(cluster.get("models") or []) or "?"
    print(f"\nSUGGESTION: {cluster['representative']}")
    print(f"  reached {cluster['frequency']}x | {cluster['model_count']} model(s): {models}"
          f" | landed gain: {cluster.get('landed_avg_improvement', 0.0):.1f}%"
          f" | +{cluster['correlation_delta']:.1f}% correlation delta")
    print(f"  PRIORITY to formalize = {cluster.get('priority_score', 0.0):.1f}"
          f"  (impact {cluster.get('landed_avg_improvement', 0.0):.1f}% × coverage {cluster.get('query_shapes', 0)} query shape(s))")
    print(f"  → {verdict}")
    variants = cluster["examples"][1:3]
    if variants:
        print(f"  variants: {variants}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Report discovery candidates against the promotion criteria")
    parser.add_argument("--min-frequency", type=int, default=3, help="Min runs a cluster must appear in (default: 3)")
    parser.add_argument("--top", type=int, default=10, help="How many top clusters to report (default: 10)")
    parser.add_argument("--write-hints", action="store_true",
                        help="TEACHER (qwen) writes + injects a teaching hint for each CANDIDATE (degrau 1)")
    args = parser.parse_args()

    print("\nLoading discovery records and clustering...")
    clusters = discover_clusters(min_frequency=args.min_frequency)
    if not clusters:
        print(f"No cluster with frequency >= {args.min_frequency}. Run more queries (discovery mode).")
        return

    backend = get_backend()
    # Order by PROMOTION PRIORITY (impact × coverage) — the human formalizes the highest-value first.
    actionable = sorted((c for c in clusters if not c["blocked"]), key=lambda c: c.get("priority_score", 0.0), reverse=True)
    blocked = [c for c in clusters if c["blocked"]]

    print(f"\n{'='*64}")
    print(f"CANDIDATE REPORT — {len(actionable)} actionable, {len(blocked)} blocked")
    print("CANDIDATE = passed (a) freq + (b) cross-model + (c) gain → human writes the detector.")
    if args.write_hints:
        print("--write-hints: qwen writes + injects the teaching hint for the CANDIDATEs (rung 1).")
    print(f"{'='*64}")

    for c in actionable[: args.top]:
        verdict, is_candidate = _verdict(c, backend)
        _print_cluster(c, verdict)
        # The hint-B (rung 1) fires on its OWN gate — teachable — NOT the detector gate. Teachable =
        # ≥1 model LANDED (worked example + proven gain) AND ≥1 OTHER model GENUINELY reached but botched
        # (SQL-evidenced, so a hallucinated label on broken SQL — e.g. llama — does not count).
        teach = evaluate_teachable(c, backend)
        if teach.teachable:
            print(f"  🎓 TEACHABLE: landed={teach.landed_models} | student(s) that reached but did not land={teach.student_models}")
        if args.write_hints and teach.teachable:
            name = _slug(c["representative"])
            hint = write_and_inject_hint(c, name)
            if hint:
                print(f"  🎓 qwen wrote + injected the hint '{name}':")
                print(f"     \"{hint[:160]}{'…' if len(hint) > 160 else ''}\"")
            else:
                print("  ⚠️  qwen produced no hint (no landed worked examples in this cluster?)")

    if blocked:
        print(f"\n{'='*64}")
        print(f"VOCABULARY GAPS — {len(blocked)} blocked (appear but gain ~0)")
        print(f"{'='*64}")
        for c in blocked[: args.top]:
            verdict, _ = _verdict(c, backend)
            _print_cluster(c, verdict)


if __name__ == "__main__":
    main()
