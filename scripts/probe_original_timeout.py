#!/usr/bin/env python3
"""Can the ORIGINALS of q1, q30 and q81 finish at full scale, given more time?

WHY THIS MATTERS
    Those three queries are the ONLY ones whose lands are PROVISIONAL. Their originals never finish
    inside the 45s gate, so the S=1 comparison falls back to the reduced-scale instance — which by
    policy #17 can REFUTE but never CERTIFY. They are also, awkwardly, among the biggest reported
    gains (96%, 98%, 99%): the flashiest results are the least verified.

    Two outcomes, both useful:
      * they DO finish with a longer budget -> the three lands become DEFINITIVE and the caveat
        disappears from every table;
      * they do NOT finish -> we have PROOF that full-scale verification is impossible for them,
        which is a stronger sentence than the current "the original timed out".

COSTS NOTHING IN API: this only executes the ORIGINAL SQL. No model is involved.

⚠️ Run it alone. A heavy query competing with a live campaign distorts both timings.

USAGE
    python scripts/probe_original_timeout.py [timeout_seconds]      # default 600
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv(".env")
from sqlalchemy import text  # noqa: E402

from src.connections import get_engine  # noqa: E402
from src.pipeline.discovery.suggestion_store import _query_hash  # noqa: E402

TARGETS = ("q1", "q30", "q81")


def main():
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    sqls = {}
    for f in glob.glob("queries/*/TPCDS/*.json"):
        name = os.path.basename(f).replace(".json", "")
        if name not in TARGETS:
            continue
        d = json.load(open(f))
        sqls[name] = d.get("sql") or d.get("query") or ""

    print(f"budget = {budget}s por execução (o gate usa 45s) · AQUECE antes e cronometra a 2a\n")
    for name in TARGETS:
        sql = sqls.get(name)
        if not sql:
            print(f"  {name}: query file not found")
            continue
        # ⚠️ AQUECER ANTES (correção 21/08). A primeira versão executava a original UMA vez e
        # concluía "impossível" — mas essa uma vez era a **FRIA**. O gate mede warm-vs-warm
        # justamente porque o custo frio é ordens de magnitude maior: o comentário do
        # `_robust_explain_analyze` cita a 29c em ~28-41 s FRIA contra ~1,8 s QUENTE, e a sonda de
        # paridade viu a q69 em >45 s fria contra 722 ms quente — 60×.
        # Concluir pela run fria pode ter marcado como incertificáveis queries que, quentes,
        # completariam. Agora: 1 execução de aquecimento (DESCARTADA) + 1 cronometrada.
        with get_engine().connect() as conn:
            conn.execute(text(f"SET statement_timeout = {budget * 1000}"))
            warm_ok, warm_dt = True, None
            t0 = time.monotonic()
            try:
                conn.execute(text(sql)).fetchall()
                warm_dt = time.monotonic() - t0
                print(f"  {name}: aquecimento (descartado) em {warm_dt:.1f}s")
            except Exception as e:
                warm_ok = False
                warm_dt = time.monotonic() - t0
                print(f"  {name}: ❌ nem o AQUECIMENTO completou em {warm_dt:.0f}s ({str(e)[:50]})")
            if not warm_ok:
                print(f"  {name}: -> verificação em escala real é IMPOSSÍVEL mesmo com aquecimento; "
                      f"a marca provisória está correta")
                continue
            t0 = time.monotonic()
            try:
                rows = conn.execute(text(sql)).fetchall()
                dt = time.monotonic() - t0
                print(f"  {name}: ✅ QUENTE completou em {dt:.1f}s · {len(rows)} linhas "
                      f"(frio {warm_dt:.1f}s → quente {dt:.1f}s = {warm_dt/max(dt,0.001):.0f}× mais rápido) "
                      f"-> o land PODE virar DEFINITIVO")
            except Exception as e:
                dt = time.monotonic() - t0
                print(f"  {name}: ❌ QUENTE não completou em {dt:.0f}s ({str(e)[:50]}) "
                      f"-> a marca provisória está correta")


if __name__ == "__main__":
    main()
