#!/usr/bin/env python
"""#10b batch runner — one auto-feed pass against the REAL suggestion store.

Reads the discovery store (SUGGESTION_STORE_DIR), clusters the LLM suggestions, curates the reliable +
teachable ones, DISTILLS each into a general rule, SEMANTIC-DEDUPs vs already-injected rules, and
injects the survivors into learned_rules (the guided arm). This is the batch that turns the #10a manual
step automatic — run it at the END of a discovery phase, never per-run (train/freeze reproducibility).

PREREQ:
  - point SUGGESTION_STORE_DIR at the rich DISCOVERY store (the one with real free-form runs), not a
    cache-curve store.
  - ollama up (embeddings for clustering + dedup; the TEACHER model for distillation).
  - AUTO_FEED=on in .env (or pass --force). --dry-run reports without mutating learned_rules.

Usage:
  python scripts/run_auto_feed.py --dry-run          # review what WOULD be injected
  python scripts/run_auto_feed.py --force            # actually inject (bypasses AUTO_FEED gate)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root → `import src`

from dotenv import load_dotenv

load_dotenv()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report what WOULD be injected, without add_rule")
    ap.add_argument("--force", action="store_true", help="bypass the AUTO_FEED env gate")
    ap.add_argument("--model-family", default=None, help="teacher for distillation (default: TEACHER_MODEL_FAMILY)")
    args = ap.parse_args()

    from src.pipeline.backends import get_backend
    from src.pipeline.discovery import auto_feed as af
    from src.pipeline.discovery.learned_rules import load_rules

    store = os.getenv("SUGGESTION_STORE_DIR", ".cache/suggestions")
    print(f"[auto-feed] store={store} · AUTO_FEED={os.getenv('AUTO_FEED', '(unset)')} · "
          f"dry_run={args.dry_run} · force={args.force}")
    print(f"[auto-feed] regras já injetadas ANTES: {len(load_rules())}")

    report = af.auto_feed(get_backend(), model_family=args.model_family,
                          force=args.force, dry_run=args.dry_run)

    counts = {}
    for r in report:
        counts[r.get("status")] = counts.get(r.get("status"), 0) + 1
    print(f"\n[auto-feed] {len(report)} clusters → {counts}\n")
    for r in report:
        st = r.get("status")
        if st in ("injected", "would_inject"):
            print(f"  ✅ {st} [{r.get('tier')}] name={r.get('name')} students={r.get('students')}")
            print(f"      regra: {r.get('hint')}")
        elif st == "skipped_duplicate":
            print(f"  ⏭  duplicata semântica (não injetada): {(r.get('hint') or '')[:90]}")
        elif st in ("rejected_criteria", "not_teachable", "no_hint"):
            print(f"  ·  {st}: {r.get('representative')}")
        elif st == "disabled":
            print("  ⚠️  AUTO_FEED off e sem --force → nada rodou (use --dry-run ou --force).")

    if not args.dry_run and args.force is False and counts.get("disabled"):
        return
    print(f"\n[auto-feed] regras injetadas DEPOIS: {len(load_rules())}")


if __name__ == "__main__":
    main()
