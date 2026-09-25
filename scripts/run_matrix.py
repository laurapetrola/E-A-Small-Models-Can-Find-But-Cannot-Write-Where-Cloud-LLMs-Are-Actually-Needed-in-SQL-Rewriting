#!/usr/bin/env python3
"""Characterization-matrix runner (the thing that replaces ~1000 manual curls).

You set MODEL_FAMILY + DB_TYPE/DB_URI (+ DISABLE_SEMANTIC_CACHE=on, APPLY_HEURISTICS) in .env, restart
the server, then run this for that config. It walks the query corpus and sends each query to /pipeline
N times; the server saves every run to .cache/suggestions (the matrix data). Then characterization_table.py
reads the store. Repeat per config (model × engine), swapping .env + restarting between.

RESILIENT: a query that errors (HTTP/timeout, or the pipeline reports failure) is LOGGED and SKIPPED —
the matrix NEVER stops. Failures → .cache/matrix_failures.log. Fix the broken (dialect) queries and
re-run ONLY them with --only.

--preflight: BEFORE the expensive matrix, just EXPLAIN each query on the engine (no LLM, seconds) to
find which break on THIS engine (rollup/||/interval dialect issues) — fix those first.

Usage:
  python scripts/run_matrix.py --preflight                 # fast: which queries break on this engine?
  python scripts/run_matrix.py --runs 3                    # full matrix for the current .env config
  python scripts/run_matrix.py --runs 3 --only q5 q40      # re-run just these (after fixing dialect)
  python scripts/run_matrix.py --dirs curated heldout      # subset of categories
  python scripts/run_matrix.py --runs 3 --resume           # skip queries already at 3 runs (this model+engine+arm); retry the rest

NOTE: the SERVER decides which model/engine runs (its env). The runner's MODEL_FAMILY/DB config only
label output and drive --resume's match — so run this in the SAME env the server uses, or --resume
mis-counts. db_type comes from get_db_type() (same source the server tags the store with).
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

URL = os.getenv("PIPELINE_URL", "http://localhost:8000/pipeline/input")
STREAM_URL = os.getenv("PIPELINE_STREAM_URL", "http://localhost:8000/pipeline/stream")
FAIL_LOG = ".cache/matrix_failures.log"


def _run_stream(sql, timeout):
    """POST to /pipeline/stream (SSE) and print each node event LIVE; return the final result dict.
    Used by --stream so you can watch schema_analyst → architect → executor in real time."""
    body = json.dumps({"sql": sql}).encode()
    req = urllib.request.Request(STREAM_URL, data=body, headers={"Content-Type": "application/json"})
    result = None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:                                   # iterate the stream line by line
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            if ev.get("done"):
                result = ev.get("result")
            else:
                node, status = ev.get("node", "?"), ev.get("status", "")
                el = ev.get("node_elapsed_s")
                tail = f" ({el:.0f}s)" if isinstance(el, (int, float)) else ""
                print(f"      ↳ {node}: {status}{tail}")
    return result or {}


def _benchmark_subdir(benchmark):
    """TPC-DS and JOB live in SEPARATE databases — only run the queries that match the connected DB_URI.
    Auto-detect: an 'imdb' DB_URI means JOB; otherwise TPC-DS. Override with --benchmark."""
    if benchmark == "both":
        return None  # no filter (only valid if both schemas are in one DB — they are not, by default)
    if benchmark in ("tpcds", "job"):
        return benchmark.upper().replace("TPCDS", "TPCDS")
    uri = (os.getenv("DB_URI") or "").lower()
    return "JOB" if "imdb" in uri or "job" in uri else "TPCDS"


def _collect(dirs, only, benchmark):
    sub = _benchmark_subdir(benchmark)
    files = []
    for d in dirs:
        pat = f"queries/{d}/{sub}/**/*.json" if sub else f"queries/{d}/**/*.json"
        files += sorted(glob.glob(pat, recursive=True))
    files = [f for f in files if os.path.basename(f) != "workload_registry.json"]
    if only:
        stems = set(only)
        files = [f for f in files if os.path.splitext(os.path.basename(f))[0] in stems]
    return files


def _stem(f):
    return os.path.splitext(os.path.basename(f))[0]


def preflight(files):
    """EXPLAIN each ORIGINAL query on the current engine — no LLM. Catches dialect breakage fast."""
    from src.pipeline.backends import get_backend
    backend = get_backend()
    engine = os.getenv("DB_TYPE", "?")
    print(f"== PREFLIGHT (EXPLAIN, no LLM) | engine={engine} | {len(files)} queries ==")
    broken = []
    for f in files:
        sql = json.load(open(f))["sql"]
        try:
            ex = backend.explain(sql)
            if ex.get("error"):
                broken.append(_stem(f)); print(f"  ❌ {_stem(f)}: {str(ex['error'])[:90]}")
            else:
                print(f"  ✅ {_stem(f)}")
        except Exception as e:
            broken.append(_stem(f)); print(f"  ❌ {_stem(f)}: {str(e)[:90]}")
    print(f"\n== {len(files)-len(broken)} ok, {len(broken)} broken on this engine ==")
    if broken:
        print(f"broken: {sorted(broken)}\n→ fix the dialect of these before running the matrix here.")


def _done_counts(model, apply_mode_now):
    """For --resume: count completed runs per query_hash already in the store for THIS
    (model, engine, arm). A run is 'done' only once the server SAVED it (it carries a
    suggestion/outcome), so HTTP/timeout failures (never saved) are correctly NOT counted →
    they get retried. db_type uses the SAME source the server tags with (get_db_type)."""
    from src.pipeline.discovery.suggestion_store import load_all
    try:
        from src.pipeline.backends import get_db_type
        eng = get_db_type() or None
    except Exception:
        eng = os.getenv("DB_TYPE") or None
    counts = {}
    baseline_timeouts = {}
    for r in load_all():
        if r.get("model") != model or (r.get("db_type") or None) != eng:
            continue
        if bool(r.get("apply_mode", False)) != apply_mode_now:  # unaided vs guided counted separately
            continue
        h = r.get("query_hash")
        if not h:
            continue
        # ⚠️ NAME IS MISLEADING, kept for schema compatibility (clarified 2026-08-24). These records
        # are written by the `except` branch below when the CLIENT's HTTP request exceeds `--timeout`
        # (2400s by default). Measured durations sit between 2177s and 2400s — i.e. they are the
        # client giving up after ~40 minutes, NOT evidence that the ORIGINAL query failed to finish.
        # The original may well have run fine; what did not finish is the whole pipeline run.
        #   Reporting them as "the original did not finish" sent me down the wrong path repeatedly on
        # 24/08. The real cause was found to be the index-validation measurement running FOUR
        # unbounded EXPLAIN ANALYZE per candidate (see `_timed_explain_analyze_on_conn`, now bounded).
        # A run without a writer is still not a measured run of the pipeline — it carries no strategy,
        # no rewrite and no outcome to count.
        # It used to be counted here, which let a restart consider a query complete on fewer real
        # runs than --runs asked for: an audit of the claim cells found 3 such queries (6_fresh0804
        # q3505b74f at 3/5; masking_ab_qwen_planon q42c4d064 at 1/3 and q5346a55b at 2/3). All three
        # were non-lands, so the effect was only ever to UNDER-count — but it silently weakens n.
        if r.get("outcome") == "timeout" and not r.get("writer"):
            baseline_timeouts[h] = baseline_timeouts.get(h, 0) + 1
            continue
        counts[h] = counts.get(h, 0) + 1
    for h, n in baseline_timeouts.items():
        print(f"   [resume] {h[:10]}: {n} client-side timeout(s) not counted as runs "
              f"({counts.get(h, 0)} real run(s) on record) — the RUN exceeded the {'{}'.format(os.getenv('RUN_HTTP_TIMEOUT_HINT', '~40min'))} "
              f"HTTP budget (this says nothing about the original query), retrying")
    return counts, baseline_timeouts


def matrix(files, runs, timeout, resume=False, stream=False):
    # The resume-match label MUST equal what the SERVER saves — the server applies the reasoning suffix
    # (+think/+nothink) and +richfb via _model_label. Using raw MODEL_FAMILY here mis-matched: a REASONING=on
    # run counted against the OFF baseline ('qwen-reasoning') and skipped everything. Reuse the SAME function.
    from src.routers.input_router import _model_label
    model = _model_label() or os.getenv("MODEL_FAMILY", "?")
    engine = os.getenv("DB_TYPE", "?")
    arm = "guided" if os.getenv("APPLY_HEURISTICS", "").strip().lower() in ("1", "true", "on", "yes") else "unaided"
    if os.getenv("DISABLE_SEMANTIC_CACHE", "").strip().lower() not in ("1", "true", "on", "yes"):
        print("⚠️  DISABLE_SEMANTIC_CACHE is NOT on in the server's .env — repeated runs may hit the cache "
              "(cache_hit skips the model). Set it + restart the server before measuring.")
    from src.pipeline.nodes.corrector import two_agent_mode, writer_label
    writer_tag = f" writer={writer_label()}" if two_agent_mode() else ""
    run_arm = os.getenv("RUN_ARM", "raw")  # raw × aided (o que o STORE grava em 'arm') — eixo DIFERENTE de heuristics
    # MASKING is applied SERVER-SIDE (corrector, cloud leg) and is NOT in _model_label → surface it in the
    # header so a masked A/B is never mistaken for a plain cloud run. NOTE: this reads the CLIENT env; the
    # SERVER is the source of truth (restart it after toggling MASKING). Confirm via the corrector log line.
    from src.pipeline.masking import masking_enabled, mask_scope
    mask_tag = f" · MASKING={mask_scope()}" if (masking_enabled() and writer_label().startswith("cloud")) else ""
    print(f"== MATRIX | model={model} engine={engine} run_arm={run_arm} · heuristics={arm}{writer_tag}{mask_tag} | {len(files)} queries × {runs} runs"
          f"{' | --resume' if resume else ''} ==")
    from src.pipeline.discovery.suggestion_store import _query_hash
    done, timed_out = _done_counts(model, arm == "guided") if resume else ({}, {})
    ok = fail = skipped = capped = 0

    # ATTEMPT CEILING (2026-08-24). A query whose ORIGINAL does not finish inside the gate budget
    # produces a baseline timeout: no strategy, no rewrite, nothing to count. `--resume` correctly
    # refuses to count it as a run — and then retries, forever. On the C1 cell that turned into a
    # schedule sink: `q51` had burnt 8 real runs plus 7 timeouts against a target of 5, `q11` 3 real
    # runs plus 9 timeouts, and one attempt on those heavy queries costs 30-40 minutes. Over one
    # 3h44 window the cell produced 4 real runs and 4 timeouts — half the wall clock bought nothing.
    #
    # The ceiling bounds attempts (real runs + baseline timeouts) at RUN_ATTEMPT_FACTOR x --runs.
    # It does NOT change any measurement: a query the engine cannot execute is a non-land either
    # way, and re-learning that a fifth time costs 35 minutes and yields the same fact.
    # ⚠️ It DOES lower the effective n for the queries it stops, so the cell doc must say which
    #    queries were capped and at what n — a capped query must never be reported as if it had the
    #    full n behind it.
    attempt_factor = float(os.getenv("RUN_ATTEMPT_FACTOR", "2"))
    attempt_budget = max(runs + 1, int(runs * attempt_factor))
    failures = []
    for f in files:
        stem = _stem(f)
        sql = json.load(open(f))["sql"]
        already = done.get(_query_hash(sql), 0) if resume else 0
        todo = max(0, runs - already)
        if resume and todo == 0:
            print(f"  {stem}: already has {already}/{runs} in the store for ({model},{engine},{arm}) — skipping")
            skipped += 1
            continue
        _to = timed_out.get(_query_hash(sql), 0) if resume else 0
        if resume and (already + _to) >= attempt_budget:
            print(f"  {stem}: ATTEMPT CEILING reached — {already} real run(s) + {_to} baseline "
                  f"timeout(s) = {already + _to} attempts, budget {attempt_budget} "
                  f"({attempt_factor:g} x {runs}). The original does not finish inside the gate; "
                  f"further retries re-measure the same fact. Stopping this query at n={already}.")
            capped += 1
            continue
        if resume and already:
            print(f"  {stem}: {already}/{runs} done — running +{todo}")
        for k in range(todo):
            i = already + k + 1
            t0 = time.time()
            try:
                if stream:
                    print(f"  {stem} run{i}: …")          # header; node events stream below it
                    res = _run_stream(sql, timeout)
                else:
                    data = json.dumps({"sql": sql}).encode()
                    req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        res = json.loads(r.read())
                appr = res.get("approved")
                pp = res.get("production_path") or ""
                em = (res.get("metrics") or {}).get("execution_time", {})
                gain = em.get("improvement_pct") if em.get("improvement_pct") is not None else em.get("improvement_lower_bound_pct")
                it = (res.get("metrics") or {}).get("reflection_iterations")
                techs = res.get("discovered_techniques") or []   # the RULE(s) the model suggested this run
                print(f"  {stem} run{i}: approved={appr} path={pp} gain={gain} iter={it} ({time.time()-t0:.0f}s)")
                if techs:
                    print(f"      rule(s): {techs}")
                ok += 1
            except Exception as e:
                msg = str(e)[:200]
                elapsed = time.time() - t0
                print(f"  {stem} run{i}: FAILED → {msg} ({elapsed:.0f}s)")
                # A TIMEOUT is a capability finding (the model produced nothing in bounded time), not just an
                # HTTP error — record it in the store as class-c (outcome="timeout") so characterization_table
                # counts it, instead of it vanishing into the failure log. Other errors stay log-only.
                if "timed out" in msg.lower() or "timeout" in msg.lower():
                    try:
                        from src.pipeline.discovery.suggestion_store import save_run
                        from src.pipeline.backends import get_db_type
                        save_run(suggestions=[], improvement_pct=None, raw_sql=sql, model=model,
                                 outcome="timeout", db_type=get_db_type(),
                                 rules_in_prompt=(["guided"] if arm == "guided" else None),  # keep apply_mode/arm correct
                                 inference_ms=round(elapsed * 1000, 1))
                    except Exception:
                        pass
                failures.append({"query": stem, "file": f, "model": model, "engine": engine,
                                 "arm": arm, "run": i, "error": msg})
                fail += 1
    if failures:
        os.makedirs(".cache", exist_ok=True)
        with open(FAIL_LOG, "a") as lg:
            for x in failures:
                lg.write(json.dumps(x) + "\n")
    print(f"\n== done: {ok} ok, {fail} failed"
          f"{f', {skipped} queries skipped (resume)' if resume else ''}"
          f"{f', {capped} CAPPED by the attempt ceiling (report their reduced n)' if capped else ''} ==")
    if failures:
        bad = sorted({x["query"] for x in failures})
        print(f"failures logged in {FAIL_LOG}. queries: {bad}")
        print(f"→ re-run only them after fixing: python scripts/run_matrix.py --runs {runs} --only {' '.join(bad)}")

    # ⛔⛔ EXIT CODE (corrigido 28/08). `matrix()` não retornava nada, então o processo saía SEMPRE 0 —
    # inclusive quando NENHUM run funcionou. Em 28/08 o C4 rodou com os containers de MySQL PARADOS:
    # 63 runs deram `HTTP 500 (0s)`, o script registrou "DONE (exit 0)" e a cadeia de nuvem seguiu
    # para o C3 como se a célula tivesse sido medida. Uma célula VAZIA ficou marcada como concluída.
    #
    # Regra: nada medido (ok == 0) com FALHAS REAIS é FALHA DURA. Falhas parciais continuam
    # saindo 0 — são normais (timeout, não-equivalência) e o `--resume` as retoma.
    #
    # ⛔⛔ BUG #23 (13/09/2026) — `capped` FOI REMOVIDO desta condição. A regra antiga era
    #    `if ok == 0 and (fail or capped)`, e ela não distinguia
    #      "nada rodou porque QUEBROU"  de  "nada rodou porque ACABOU".
    #    O L4 fechou em 13/09 com 20 queries em 5/5 (skipped) + a `q11` CAPADA pelo teto: zero runs
    #    novos, zero falhas — a célula estava COMPLETA. A guarda leu o `capped` como falha, saiu 2,
    #    e a cadeia gastou as 3 tentativas do `perna()` (~15 min) numa FALSA falha antes de seguir.
    #    ⚠️ Pior que o tempo: a perna ficou registrada no log como "desistindo após 3 falhas reais",
    #       o que faria qualquer auditoria futura acreditar que o L4 não fechou.
    #
    # ⭐ Uma query CAPADA é conclusão legítima — é o teto de tentativas funcionando como projetado.
    #    O que ela exige é REPORTE com n reduzido (o doc da célula diz qual query e em que n), não
    #    um código de erro. Só `fail` (run tentado que quebrou) justifica falha dura.
    if ok == 0 and fail:
        print("⛔ NENHUM run foi medido e houve FALHAS — saindo com codigo 2 para a cadeia NAO tratar como concluida.")
        return 2
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--dirs", nargs="*", default=["curated", "heldout", "non-curated"])
    ap.add_argument("--only", nargs="*", default=None, help="query stems (e.g.: q5 13d)")
    ap.add_argument("--timeout", type=int, default=2400, help="timeout per request in s (slow models)")
    ap.add_argument("--preflight", action="store_true", help="only EXPLAIN each query (finds dialect breakage, no LLM)")
    ap.add_argument("--resume", action="store_true", help="skips queries that already have --runs runs in the store for this (model, engine, arm); re-runs only what's missing")
    ap.add_argument("--stream", action="store_true", help="use /pipeline/stream and print each node LIVE (schema_analyst → architect → executor) instead of the blocking POST")
    ap.add_argument("--benchmark", choices=["tpcds", "job", "both", "auto"], default="auto",
                    help="which benchmark to run (TPC-DS and JOB are in separate DBs); auto = detect from DB_URI")
    args = ap.parse_args()

    bench = args.benchmark if args.benchmark != "auto" else "auto"
    files = _collect(args.dirs, args.only, bench if bench != "auto" else None)
    if not files:
        print("no queries found (check --dirs/--only)."); return
    rc = (preflight if args.preflight else lambda fs: matrix(fs, args.runs, args.timeout, args.resume, args.stream))(files)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
