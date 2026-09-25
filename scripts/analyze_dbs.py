"""Run ANALYZE on the primary (DB_URI) and verification (VERIFY_DB_URI) instances.

WHY this exists: the equivalence gate (S=1) and the verification instance (SF1) depend on the
PLANNER picking a sane plan. On a freshly-loaded or rarely-used instance the statistics are stale
→ a bad plan → a heavy query (e.g. TPC-DS q11's 4-way self-join) can take MINUTES or time out,
even on small data — which silently breaks verification (the original can't be hashed in budget).
ANALYZE is a ONE-TIME setup step (the benchmark data is static), not a per-run cost.

Run after loading/refreshing data into any instance:
    python scripts/analyze_dbs.py
Reads DB_URI and VERIFY_DB_URI from the environment (same vars the app uses). Safe + idempotent;
ANALYZE takes a light lock and does not block reads.
"""
import os
import time

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()  # read DB_URI / VERIFY_DB_URI from .env (same as the server), not just the shell env


def analyze(label: str, uri: str | None) -> None:
    if not uri:
        print(f"  {label}: (not set — skipped)")
        return
    try:
        engine = create_engine(uri)
        is_mysql = uri.startswith("mysql")
        with engine.connect() as conn:
            conn.execute(text("commit"))  # leave any implicit tx so ANALYZE runs outside it
            t = time.time()
            if is_mysql:
                # MySQL has no bare ANALYZE — it's per-table: ANALYZE TABLE t1, t2, ...
                tables = [r[0] for r in conn.execute(text("SHOW TABLES")).fetchall()]
                if tables:
                    conn.execute(text("ANALYZE TABLE " + ", ".join(f"`{t}`" for t in tables)))
                note = f"{len(tables)} tables"
            else:
                conn.execute(text("ANALYZE"))  # PostgreSQL: whole-DB
                note = "whole-DB"
            print(f"  {label}: ANALYZE ok ({note}) in {time.time() - t:.1f}s  ({uri.split('@')[-1]})")
    except Exception as e:
        print(f"  {label}: ERROR — {str(e)[:120]}")


if __name__ == "__main__":
    print("Refreshing planner statistics (ANALYZE):")
    analyze("primary (DB_URI)", os.getenv("DB_URI"))
    analyze("verify  (VERIFY_DB_URI)", os.getenv("VERIFY_DB_URI"))
    print("Done. Re-run after any data load/refresh.")
