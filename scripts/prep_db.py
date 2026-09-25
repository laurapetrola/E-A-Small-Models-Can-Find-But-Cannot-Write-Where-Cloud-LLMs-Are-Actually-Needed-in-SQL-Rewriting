#!/usr/bin/env python
"""DB-switch tooling — prepara e VALIDA a troca de banco (evita a falha SILENCIOSA do ANALYZE).

O erro que isto previne (custou 1 dia uma vez): ao apontar DB_URI/VERIFY_DB_URI novos SEM rodar ANALYZE,
o planner fica flat → FIND degrada + original trava no S=1 → 0 land em TUDO, e parece "modelo ruim"
quando é só estatística. Fluxo seguro:

  1. `python scripts/prep_db.py --show tpcds-mysql`   → imprime o bloco .env do alvo (cole no .env)
  2. (edite .env, REINICIE o servidor)
  3. `python scripts/prep_db.py`                      → ANALYZE main+verify + CANÁRIO (stats presentes?)

O canário confirma, ANTES de rodar a campanha, que as stats existem (senão PARA e avisa).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

# Presets (portas do .env.example). VERIFY vazio = sem instância de verificação (IMDb).
PRESETS = {
    "tpcds-pg":    ("postgresql://postgres:mysecretpassword@localhost:5435/tpcds",
                    "postgresql://postgres:mysecretpassword@localhost:5437/tpcds", "postgres"),
    "tpcds-mysql": ("mysql+pymysql://root:mysecretpassword@localhost:3308/tpcds",
                    "mysql+pymysql://root:mysecretpassword@localhost:3309/tpcds", "mysql"),
    "imdb-pg":     ("postgresql://postgres:postgres@localhost:5436/imdb", "", "postgres"),
    "imdb-mysql":  ("mysql+pymysql://root:mysql@localhost:3307/imdb", "", "mysql"),
}


def show(target: str) -> None:
    if target not in PRESETS:
        sys.exit(f"alvo desconhecido '{target}'. opções: {', '.join(PRESETS)}")
    uri, verify, dbtype = PRESETS[target]
    bench = "tpcds" if "tpcds" in target else "imdb"
    print(f"# --- bloco .env para {target} (cole no .env e REINICIE o servidor) ---")
    print(f"DB_TYPE={dbtype}")
    print(f"DB_URI={uri}")
    print(f"VERIFY_DB_URI={verify}" + ("" if verify else "   # (sem verify neste alvo)"))
    print(f"SUGGESTION_STORE_DIR=.cache/suggestions_{target.replace('-', '_')}")
    print(f"INDEX_SUGGESTION_STORE_DIR=.cache/index_suggestions_{target.replace('-', '_')}")
    print(f"# depois: python scripts/analyze_dbs.py  (ou este script sem args) — {bench}")


def _canary(label: str, uri: str | None) -> bool:
    """Após o ANALYZE, as stats do planner existem? PG: linhas em pg_stats (schema do usuário). MySQL:
    table_rows populado na maior tabela. Retorna True se OK. Vazio (sem verify) = pula (não falha)."""
    if not uri:
        print(f"  {label}: (não setado — pulado)")
        return True
    try:
        engine = create_engine(uri)
        with engine.connect() as conn:
            conn.execute(text("commit"))
            if uri.startswith("mysql"):
                row = conn.execute(text(
                    "SELECT table_name, table_rows FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() ORDER BY table_rows DESC LIMIT 1")).fetchone()
                ok = bool(row and (row[1] or 0) > 0)
                detail = f"maior tabela {row[0]}={row[1]} linhas" if row else "sem tabelas"
            else:
                n = conn.execute(text(
                    "SELECT count(*) FROM pg_stats WHERE schemaname NOT IN "
                    "('pg_catalog','information_schema')")).scalar()
                mx = conn.execute(text(
                    "SELECT relname, reltuples::bigint FROM pg_class WHERE relkind='r' "
                    "ORDER BY reltuples DESC LIMIT 1")).fetchone()
                ok = (n or 0) > 0 and bool(mx and (mx[1] or 0) > 0)
                detail = f"{n} colunas com stats · maior tabela {mx[0]}={mx[1]} linhas" if mx else f"{n} stats"
        mark = "✅ stats OK" if ok else "❌ FLAT (stats ausentes — NÃO rode a campanha!)"
        print(f"  {label}: {mark} — {detail}  ({uri.split('@')[-1]})")
        return ok
    except Exception as e:
        print(f"  {label}: ERRO — {str(e)[:120]}")
        return False


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--show":
        show(sys.argv[2] if len(sys.argv) > 2 else "")
        return
    from scripts.analyze_dbs import analyze
    print(f"DB atual: DB_TYPE={os.getenv('DB_TYPE')} · {(os.getenv('DB_URI') or '?').split('@')[-1]}")
    print("\n1) ANALYZE:")
    analyze("primary (DB_URI)", os.getenv("DB_URI"))
    analyze("verify  (VERIFY_DB_URI)", os.getenv("VERIFY_DB_URI"))
    print("\n2) CANÁRIO (stats presentes?):")
    ok_main = _canary("primary", os.getenv("DB_URI"))
    ok_verify = _canary("verify ", os.getenv("VERIFY_DB_URI"))
    print()
    if ok_main and ok_verify:
        print("✅ PRONTO — stats OK no main e verify. Pode rodar a campanha.")
    else:
        print("❌ PARE — stats ausentes. Rode ANALYZE / cheque o DB antes de qualquer run (falha silenciosa).")
        sys.exit(1)


if __name__ == "__main__":
    main()
