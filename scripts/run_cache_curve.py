#!/usr/bin/env python
"""#12 validated-strategy cache — ONLINE self-improvement curve (Paper 2 deliverable).

Processes an ORDERED workload one query at a time against the running pipeline (:8000). Because the
cache auto-persists every reliable land (STRATEGY_CACHE_PERSIST=on), each land makes a later query of
the SAME shape a cache HIT — bypassing reasoning. This script traces the curve that PROVES the system
self-feeds:  queries processed × (hit-rate ↑ · avg inference latency ↓ · cumulative lands).

PREREQ (server reads .env at STARTUP → restart after setting):
  STRATEGY_CACHE=on · STRATEGY_CACHE_PERSIST=on · TWO_AGENT_MODE=on · REASONING=on (so a bypass is a
  visible latency win) · a reliable writer (WRITER_BACKEND=cloud) so lands actually happen.

Usage:
  python scripts/run_cache_curve.py --reset-cache --queries q1 q30 q81 q69 q5 q38 --out documentation/experiments/cache_curve.md
    - order matters: put a shape's FIRST lander early; its siblings later become hits.
    - --reset-cache starts COLD (the honest curve: cold → warming). Omit to continue an existing cache.
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root → `import src`

from dotenv import load_dotenv

load_dotenv()  # o header do doc lê os levers do .env (mesmo setup que o servidor) — senão sai "raw/local" default

URL = os.getenv("PIPELINE_URL", "http://localhost:8000/pipeline/input")


def load_sql(stem: str) -> str:
    hits = sorted(glob.glob(f"queries/**/{stem}.json", recursive=True))
    if not hits:
        raise SystemExit(f"query '{stem}' not found under queries/")
    return json.load(open(hits[0]))["sql"]


def post(sql: str, timeout: int) -> dict:
    data = json.dumps({"sql": sql}).encode()
    req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", nargs="+", required=True, help="ORDERED query stems (e.g. q1 q30 q69 q5 q38)")
    ap.add_argument("--reset-cache", action="store_true", help="clear the strategy cache first (cold start)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default="documentation/experiments/cache_curve.md")
    args = ap.parse_args()

    if args.reset_cache:
        from src.pipeline.discovery import strategy_cache as sc
        import shutil
        shutil.rmtree(sc.CACHE_DIR, ignore_errors=True)
        print(f"[curve] cache reset (cold start): {sc.CACHE_DIR}")

    rows = []
    hits = lands = 0
    lat_sum = 0.0
    print(f"[curve] {len(args.queries)} queries, sequential (each land feeds the cache)\n")
    for i, stem in enumerate(args.queries, 1):
        sql = load_sql(stem)
        t0 = time.time()
        try:
            res = post(sql, args.timeout)
            hit = bool(res.get("strategy_cache_hit"))
            appr = bool(res.get("approved"))
            infer = (res.get("metrics") or {}).get("total_inference_ms")
            em = (res.get("metrics") or {}).get("execution_time") or {}
            gain = em.get("improvement_pct") if em.get("improvement_pct") is not None else em.get("improvement_lower_bound_pct")
        except Exception as e:
            print(f"{i:2} {stem:6} FAILED → {str(e)[:80]}")
            rows.append({"i": i, "q": stem, "hit": None, "land": None, "infer_ms": None, "gain": None})
            continue
        hits += int(hit)
        lands += int(appr)
        lat_sum += (infer or 0)
        rows.append({"i": i, "q": stem, "hit": hit, "land": appr, "infer_ms": infer, "gain": gain,
                     "cum_hit_rate": hits / i, "cum_lands": lands, "cum_avg_infer_ms": lat_sum / i})
        print(f"{i:2} {stem:6} hit={str(hit):5} land={str(appr):5} infer={infer}ms gain={gain} "
              f"| CUM hit-rate={hits / i:.0%} lands={lands} avg_infer={lat_sum / i:.0f}ms  ({time.time() - t0:.0f}s)")

    _write_md(args.out, rows, args.queries)
    print(f"\n[curve] wrote {args.out}")


def _write_md(path: str, rows: list[dict], order: list[str]):
    done = [r for r in rows if r.get("hit") is not None]
    n = len(done) or 1
    hit_rate = sum(1 for r in done if r["hit"]) / n
    land_rate = sum(1 for r in done if r["land"]) / n
    def _env(k, d=""):
        return os.getenv(k, d)
    _arm = "aided/2-agentes" if _env("TWO_AGENT_MODE", "").lower() in ("on", "1", "true") else "raw"
    _guided = "guiado(hint-B)" if _env("APPLY_HEURISTICS", "").lower() in ("on", "1", "true") else "unaided"
    _writer = _env("WRITER_BACKEND", "local")
    _reason = "on" if _env("REASONING", "").lower() in ("on", "1", "true") else "off"
    _plan = "off" if _env("PLAN_HINTS", "on").lower() in ("off", "0", "false") else "on"
    setup = (f"arm: {_arm} · {_guided} · writer: {_writer} coder · reasoning: {_reason} · plano: {_plan} · "
             f"STRATEGY_CACHE: on · engine: PostgreSQL · TPC-DS")
    # Data REAL do run (timestamps do store), não "gerado hoje" — staleness à primeira vista.
    try:
        from src.pipeline.discovery.suggestion_store import run_dates
        rd = run_dates(os.getenv("SUGGESTION_STORE_DIR"))
        data = (f"{rd['first']}" if rd["first"] == rd["last"] else f"{rd['first']}–{rd['last']}") if rd["n"] else "?"
    except Exception:
        data = "?"
    lines = [
        "# #12 Validated-strategy cache — curva de auto-melhoria online",
        "",
        f"> 📅 **Rodado em:** {data}",
        f"> **Setup:** `{setup}`",
        "",
        f"> Workload processado EM SEQUÊNCIA (cada land alimenta o cache). Ordem: `{' '.join(order)}`.",
        "> A curva prova a auto-alimentação: conforme os lands populam o cache, queries de MESMA forma viram",
        "> HIT (pulam o reasoning) → hit-rate sobe e a latência média de inferência cai, sem perder land-rate.",
        "",
        f"**Resumo:** {n} queries · hit-rate final **{hit_rate:.0%}** · land-rate **{land_rate:.0%}**.",
        "",
        "| # | query | cache hit | landou | inferência (ms) | ganho % | hit-rate acum. | lands acum. | inferência média acum. (ms) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r.get("hit") is None:
            lines.append(f"| {r['i']} | {r['q']} | — | — | FAIL | — | — | — | — |")
        else:
            lines.append(
                f"| {r['i']} | {r['q']} | {'✅' if r['hit'] else '·'} | {'✅' if r['land'] else '·'} | "
                f"{r['infer_ms']} | {r['gain']} | {r['cum_hit_rate']:.0%} | {r['cum_lands']} | {r['cum_avg_infer_ms']:.0f} |"
            )
    lines += [
        "",
        "## Leitura",
        "- **hit-rate acum. ↑** = o cache está aprendendo (formas já vistas pulam o reasoning).",
        "- **inferência média acum. ↓** = a eficiência ganha (o bypass é reasoning-zero).",
        "- **lands acum.** deve seguir subindo (o land-rate NÃO cai — a validação S=1 continua gateando cada hit).",
        "- ⚠️ held-out: rode com `STRATEGY_CACHE_PERSIST=off` num set B após popular no set A → mede transferência a queries NOVAS.",
    ]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
