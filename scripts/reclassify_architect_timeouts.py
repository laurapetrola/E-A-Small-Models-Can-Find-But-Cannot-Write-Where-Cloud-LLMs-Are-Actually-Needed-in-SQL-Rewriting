#!/usr/bin/env python3
"""Post-hoc reclassify ARCHITECT-TIMEOUT runs that were saved as `mechanics_failed`.

WHY: the `architect_timed_out` flag was DROPPED by LangGraph (not declared in PipelineState — bug #10),
so a run where the architect hit ARCHITECT_TIMEOUT_S (450s) and produced NO complete attempt was saved
as `mechanics_failed` (class-b WRITE-botch) instead of `timeout` (a latency/practicality bucket, NOT a
WRITE failure). The flag is fixed for FUTURE runs; this recovers PAST ones from the saved signal:
    outcome == "mechanics_failed"  AND
    (no output: "produced no output" in errors OR empty reached_sql/optimized_sql)  AND
    inference_ms >= THRESHOLD_MS  (near the single-call 450s cap; cumulative across architect calls)

It is CONSERVATIVE: a mechanics_failed that PRODUCED output (a real botched attempt at/before the cap)
stays class-b. Only the "no-output & slow" strong signal is relabeled.

Audit: writes `outcome="timeout"` + keeps `original_outcome` + `reclassified_reason` so the change is
visible and reversible. REPORT-ONLY by default; pass --apply to write.

    python scripts/reclassify_architect_timeouts.py            # dry-run: list what WOULD change
    python scripts/reclassify_architect_timeouts.py --apply    # write the relabel + audit fields
"""
import glob
import json
import os
import sys

STORE = os.getenv("SUGGESTION_STORE_DIR", ".cache/suggestions")  # raw=default; aided seta a env
THRESHOLD_MS = float(os.getenv("ARCHITECT_TIMEOUT_RECLASS_MS", "440000"))  # 440s (cap is 450s)


def _is_architect_timeout(d: dict) -> bool:
    if d.get("outcome") != "mechanics_failed":
        return False
    errs = " ".join(d.get("errors") or []).lower()
    no_output = ("produced no output" in errs) or not (d.get("reached_sql") or d.get("optimized_sql"))
    slow = (d.get("inference_ms") or 0) >= THRESHOLD_MS
    return no_output and slow


def main() -> None:
    apply = "--apply" in sys.argv
    hits = []
    for f in sorted(glob.glob(f"{STORE}/*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get("outcome") == "timeout" and d.get("reclassified_reason"):
            continue  # already reclassified — idempotent
        if _is_architect_timeout(d):
            hits.append((f, d))

    print(f"{'APLICANDO' if apply else 'DRY-RUN (use --apply p/ escrever)'} | limiar={int(THRESHOLD_MS/1000)}s\n")
    if not hits:
        print("Nenhum registro mechanics_failed com assinatura de timeout do architect. Nada a fazer.")
        return

    per_model: dict[str, int] = {}
    for f, d in hits:
        per_model[d.get("model", "?")] = per_model.get(d.get("model", "?"), 0) + 1
        print(f"  {os.path.basename(f)} | model={d.get('model')} | inference_ms={int((d.get('inference_ms') or 0)/1000)}s "
              f"| mechanics_failed → timeout")
        if apply:
            d["original_outcome"] = d.get("outcome")
            d["outcome"] = "timeout"
            d["reclassified_reason"] = "architect_timeout_recovered (bug#10: architect_timed_out flag was dropped)"
            json.dump(d, open(f, "w"), ensure_ascii=False, indent=2)

    print(f"\n{'RE-ROTULADOS' if apply else 'A re-rotular'}: {len(hits)} registros — por modelo: {per_model}")
    if not apply:
        print("\n(nada foi escrito — rode com --apply para confirmar)")


if __name__ == "__main__":
    main()
