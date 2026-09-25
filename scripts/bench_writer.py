#!/usr/bin/env python3
"""Standalone writer benchmark — LATENCY and COST per call, paired across models.

WHY THIS EXISTS
    The pro-vs-flash writer ablation ended in a quality tie, so the decision falls to speed and cost
    — and neither is recoverable from the cells:
      * `writer_inference_ms` was dropped by LangGraph (undeclared in PipelineState) until 2026-08-15,
        so every cell recorded 0.0 and `total_inference_ms` summed only the LOCAL components;
      * token usage was never persisted at all, so "the cheaper model is cheaper" rested on the
        provider's price list, not on our measurement.
    Instrumentation is now in place but does NOT retroact, so this benchmark is the only way to
    decide the ablation with data.

WHY NOT USE WALL-CLOCK FROM THE CELLS
    Because it does not measure the writer. On the fresh battery flash looked ~32% faster per run,
    yet the instrumented components were equal or LARGER for flash (the local FIND — identical by
    construction — varied 14% between cells, i.e. machine load), and the S=1 gate dominates a run
    (full-scale execution, median of 3, plus SF1 adjudication). The two cells also ran on different
    days. Wall-clock cannot support a claim about the writer.

METHOD
    * Payloads are the REAL masked prompts, rebuilt exactly as corrector.py builds them
      (build_mask_map -> mask_sql/mask_text -> build_generate_prompt), from the strategies the
      architect actually produced in the canonical cell. Not synthetic prompts.
    * PAIRED: the same payload goes to both models.
    * INTERLEAVED, with the order alternating per payload, so provider load and time-of-day drift
      hit both arms equally. Running all of model A then all of model B would repeat exactly the
      confound that makes the cells' wall-clock unusable.
    * Reports MEDIAN (not mean): API latency is long-tailed and one stall would dominate a mean.

COST
    Token counts are MEASURED and reported as such. Money is derived, never assumed: pass the
    provider's prices explicitly, e.g.
        --price-in-pro 0.28 --price-out-pro 0.42 --price-in-flash 0.07 --price-out-flash 0.11
    (USD per 1M tokens). Without them the script reports tokens only and says so — a price pulled
    from memory is not a measurement and must not enter the paper.

USAGE
    python scripts/bench_writer.py                      # 21 payloads, 2 rounds, tokens only
    python scripts/bench_writer.py --rounds 3 --limit 10
"""
import argparse
import json
import glob
import os
import random
import re
import statistics as st
import subprocess
import sys
import time

import sqlglot

sys.path.insert(0, ".")

# Load .env FIRST. The server does this at startup (src/main.py); a standalone script does not, and
# without it MASKING/MASK_SCOPE are unset, build_mask_map returns an empty map, and the benchmark
# would silently measure UNMASKED payloads — different length and content from what the pipeline
# actually sends, which would invalidate every token count here. Caught during validation.
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

SOURCE_CELL = ".cache/suggestions_system_pro_tpcds_pg"  # canonical cell: real strategies, real SQL
MODELS = ["deepseek-v4-pro", "deepseek-v4-flash"]


def _guard_no_campaign_running():
    """Refuse to run while a campaign is live: both would contend for the same API, and the
    benchmark would measure queueing rather than the model."""
    try:
        out = subprocess.run(["pgrep", "-f", "run_matrix.py"], capture_output=True, text=True)
        if out.stdout.strip():
            sys.exit("ABORT: run_matrix.py is running — the benchmark would measure contention, "
                     "not the writer. Wait for the cell to finish.")
    except FileNotFoundError:
        pass


def build_payloads(limit):
    """Rebuild the masked prompt exactly as corrector.py does, one per distinct query."""
    from src.pipeline import masking as _mask
    from src.pipeline.backends import get_backend
    backend = get_backend()
    dialect = backend.sqlglot_dialect()
    seen, payloads = set(), []
    for path in sorted(glob.glob(f"{SOURCE_CELL}/*.json")):
        try:
            rec = json.load(open(path))
        except Exception:
            continue
        h, sql, strat = rec.get("query_hash"), rec.get("raw_sql"), rec.get("strategy")
        if not (h and sql and strat) or h in seen:
            continue
        seen.add(h)
        mm = _mask.build_mask_map(sql, dialect=dialect)
        try:
            schema = backend.relevant_schema_block(sql)  # schema-linking is ON in the canonical config
        except Exception:
            schema = ""
        if schema:
            # Mirror corrector.py: the schema block's identifiers must enter the map before it is
            # masked. Skipping this would benchmark the PRE-FIX payload — which carried ~85 real
            # identifiers and is therefore both longer and no longer what the system sends.
            _mask.extend_mask_map(mm, _mask.identifiers_in_schema_block(schema))
        prompt = backend.build_generate_prompt(
            original=_mask.mask_sql(sql, mm, dialect=dialect),
            strategy=_mask.mask_text(strat, mm),
            errors=[], history="", schema_context=_mask.mask_text(schema, mm))
        leaks = _mask.find_leaks(prompt, mm)
        if leaks:
            print(f"  WARNING {h[:8]}: masking leak in payload — {leaks[:5]}")
        payloads.append({"q": h[:8], "prompt": prompt})
        if len(payloads) >= limit:
            break
    return payloads


_CLIENT = None


def _client():
    """ONE client for the whole run, with an explicit timeout.

    The first version built a fresh client per call and read the key via os.popen each time. Two
    consequences, both observed: sockets piled up in CLOSE-WAIT (no client was ever closed) and, with
    no timeout configured, a single stalled request hung the process indefinitely — it sat 5.5h on
    one connection while the API itself answered in 2.3s. A benchmark that can hang forever is worse
    than no benchmark: it silently blocks everything queued behind it.
    """
    global _CLIENT
    if _CLIENT is None:
        from openai import OpenAI
        with open(".env") as fh:
            key = next((ln.split("=", 1)[1].strip() for ln in fh
                        if ln.startswith("KEY_DEEPSEEK=")), "")
        _CLIENT = OpenAI(api_key=key,
                         base_url=os.getenv("CLOUD_CODER_API_BASE") or "https://api.deepseek.com",
                         timeout=180.0, max_retries=2)
    return _CLIENT


def call(model, prompt):
    """One writer call. Returns latency and the token counts the provider billed."""
    client = _client()
    t0 = time.monotonic()
    r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}],
                                       temperature=0)
    ms = (time.monotonic() - t0) * 1000.0
    u = r.usage
    body = r.choices[0].message.content or ""
    return {"ms": ms, "tin": u.prompt_tokens, "tout": u.completion_tokens,
            "chars": len(body), "sql": _extract_sql(body), "parses": _parses(body)}


def _extract_sql(body):
    """The SQL the writer produced, normalised so formatting noise does not count as a difference."""
    m = re.search(r"```(?:sql)?\s*(.+?)```", body, re.S)
    sql = (m.group(1) if m else body).strip()
    return re.sub(r"\s+", " ", sql).rstrip(";").lower()


def _parses(body):
    """Does the output parse as SQL at all? A cheap proxy for the `mechanics_failed` outcome the
    cells report — measured here per model, on identical inputs, which the cells cannot isolate."""
    try:
        sqlglot.parse_one(_extract_sql(body), read="postgres")
        return True
    except Exception:
        return False


def main():
    # unbuffered: the log must show progress live, or a stall looks identical to work
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2, help="repeats per payload per model")
    ap.add_argument("--limit", type=int, default=21, help="how many distinct queries to use")
    for m in ("pro", "flash"):
        ap.add_argument(f"--price-in-{m}", type=float, default=None, help=f"USD per 1M input tokens ({m})")
        ap.add_argument(f"--price-out-{m}", type=float, default=None, help=f"USD per 1M output tokens ({m})")
    ap.add_argument("--out", default="results/writer_benchmark.json")
    args = ap.parse_args()

    _guard_no_campaign_running()
    print("building real masked payloads from the canonical cell...")
    payloads = build_payloads(args.limit)
    print(f"  {len(payloads)} payloads · median prompt {st.median(len(p['prompt']) for p in payloads):.0f} chars\n")

    res = {m: [] for m in MODELS}
    random.seed(0)
    for rnd in range(args.rounds):
        for i, p in enumerate(payloads):
            order = MODELS if (i + rnd) % 2 == 0 else MODELS[::-1]  # alternate: cancels drift
            for m in order:
                try:
                    r = call(m, p["prompt"])
                    r["q"] = p["q"]
                    res[m].append(r)
                    print(f"  r{rnd+1} {p['q']} {m:<20} {r['ms']/1000:>6.1f}s  "
                          f"in={r['tin']:>6} out={r['tout']:>5}")
                except Exception as e:
                    print(f"  r{rnd+1} {p['q']} {m:<20} FAILED: {str(e)[:90]}")
            # Persist after EVERY payload. A real call takes ~3.6 min and returns ~18k output tokens,
            # so a full run is hours: writing only at the end means any interruption throws away all
            # of it. That already happened once — a run 90% complete was killed and lost.
            os.makedirs("results", exist_ok=True)
            json.dump({"partial": True, "raw": res}, open(args.out, "w"), indent=2)

    print("\n" + "=" * 74)
    print(f"{'model':<22}{'lat median':>12}{'tok in':>10}{'tok out':>10}{'calls':>8}")
    print("-" * 74)
    summary = {}
    for m in MODELS:
        rs = res[m]
        if not rs:
            continue
        summary[m] = {
            "latency_ms_median": st.median(r["ms"] for r in rs),
            "tokens_in_median": st.median(r["tin"] for r in rs),
            "tokens_out_median": st.median(r["tout"] for r in rs),
            "calls": len(rs),
        }
        s = summary[m]
        print(f"{m:<22}{s['latency_ms_median']/1000:>11.1f}s{s['tokens_in_median']:>10.0f}"
              f"{s['tokens_out_median']:>10.0f}{s['calls']:>8}")

    # Paired per-payload comparison — the number that survives provider load, since both models saw
    # the same payload within the same interleaved slot.
    pairs = []
    for q in {r["q"] for r in res[MODELS[0]]}:
        a = [r["ms"] for r in res[MODELS[0]] if r["q"] == q]
        b = [r["ms"] for r in res[MODELS[1]] if r["q"] == q]
        if a and b:
            pairs.append(st.median(b) / st.median(a))
    if pairs:
        print(f"\nPAIRED latency ratio {MODELS[1]} / {MODELS[0]}: median {st.median(pairs):.2f}× "
              f"({sum(1 for p in pairs if p < 1)}/{len(pairs)} payloads faster for {MODELS[1]})")

    # DETERMINISM at temperature 0 — free, since the rounds were already paid for. It matters: the
    # whole n=5 regime exists because the pipeline is stochastic, and a writer that returns different
    # SQL for an identical payload injects variance into every cell downstream.
    if args.rounds > 1:
        print("\nDeterminism at temperature 0 (identical SQL across rounds, same payload):")
        for m in MODELS:
            byq = {}
            for r in res[m]:
                byq.setdefault(r["q"], []).append(r["sql"])
            stable = sum(1 for v in byq.values() if len(v) > 1 and len(set(v)) == 1)
            multi = sum(1 for v in byq.values() if len(v) > 1)
            if multi:
                summary.setdefault(m, {})["determinism"] = stable / multi
                print(f"  {m:<22} {stable}/{multi} payloads returned byte-identical SQL")

    print("\nOutput parses as SQL (proxy for the cells' mechanics_failed):")
    for m in MODELS:
        rs = res[m]
        if rs:
            ok = sum(1 for r in rs if r["parses"])
            summary.setdefault(m, {})["parse_rate"] = ok / len(rs)
            print(f"  {m:<22} {ok}/{len(rs)}")

    prices = {"deepseek-v4-pro": (args.price_in_pro, args.price_out_pro),
              "deepseek-v4-flash": (args.price_in_flash, args.price_out_flash)}
    if all(v is not None for m in MODELS for v in prices[m]):
        print("\nCOST per rewrite (USD, from the prices PASSED IN — declare the source in the paper):")
        for m in MODELS:
            pin, pout = prices[m]
            s = summary[m]
            usd = s["tokens_in_median"] / 1e6 * pin + s["tokens_out_median"] / 1e6 * pout
            summary[m]["usd_per_rewrite"] = usd
            print(f"  {m:<22} ${usd:.5f}")
    else:
        print("\nCOST: not computed — no prices passed. Tokens above are MEASURED; money is not. "
              "Pass --price-in-* / --price-out-* to derive it, and cite the price source.")

    os.makedirs("results", exist_ok=True)
    json.dump({"summary": summary, "raw": res}, open(args.out, "w"), indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
