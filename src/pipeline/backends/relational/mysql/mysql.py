import hashlib
import json
import logging
import os
import re
import time

log = logging.getLogger(__name__)


def _index_validation_executed() -> bool:
    """INDEX_VALIDATION=executed mede o índice candidato por EXECUÇÃO em vez de confiar no custo
    do planner. Espelha `postgres.py::_index_validation_executed` — mesma variável, mesmo default.

    O default segue `simulated` para que toda célula medida antes desta mudança conserve o
    significado exato que tinha. ⛔ Os dois regimes NÃO são comparáveis e nunca devem ser somados
    na mesma tabela.
    """
    return os.getenv("INDEX_VALIDATION", "simulated").strip().lower() in (
        "executed", "execute", "real", "measured"
    )

from sqlalchemy import text

from src.connections import get_engine
from src.pipeline.backends.relational.sql import SQLBackend


class MySQLBackend(SQLBackend):
    """MySQL-specific backend (MySQL 8.0+)."""

    def sqlglot_dialect(self) -> str:
        return "mysql"

    def bucket_agg_expr(self, agg_node, pred_node) -> str:
        """MySQL has no `FILTER (WHERE ...)` — express the per-bucket conditional aggregate of the
        consolidation transform as `AGG(CASE WHEN pred THEN arg END)` (and `COUNT(CASE WHEN pred THEN 1
        END)` for `COUNT(*)`). Same semantics as the PostgreSQL FILTER form; this is the ONLY engine
        difference, isolated here so the shared builder stays dialect-free."""
        import sqlglot.expressions as exp
        d = self.sqlglot_dialect()
        pred = pred_node.sql(dialect=d)
        func = agg_node.key.upper()
        arg = agg_node.this
        if func == "COUNT" and (arg is None or isinstance(arg, exp.Star)):
            return f"COUNT(CASE WHEN {pred} THEN 1 END)"
        arg_sql = arg.sql(dialect=d) if arg is not None else ""
        return f"{func}(CASE WHEN {pred} THEN {arg_sql} END)"

    def _info_schema_filter(self) -> str:
        return "table_schema = DATABASE()"

    def _table_columns(self, table: str) -> set[str]:
        """Lowercased column names of `table` (for partial-predicate validation)."""
        try:
            engine = get_engine()
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    f"WHERE table_name = :t AND {self._info_schema_filter()}"
                ), {"t": table}).fetchall()
                return {r[0].lower() for r in rows}
        except Exception:
            return set()

    def syntax_hint(self) -> str:
        return (
            "Syntax rules for MySQL:\n"
            "- Structure: SELECT ... FROM table1 JOIN table2 ON condition ... WHERE filters\n"
            "- JOIN clauses always come before WHERE — never after\n"
            "- Each JOIN must have an ON clause\n"
            "- No trailing semicolon\n"
            "- Subqueries must be aliased: (SELECT ...) AS alias\n"
            "- Use backticks for reserved-word identifiers: `order`, `group`, etc.\n"
            "- CTEs (WITH) are supported in MySQL 8.0+"
        )

    def dialect_rule_map(self) -> list[tuple[tuple[str, ...], str]]:
        """Lever D: (assinatura do erro do MySQL → regra corretiva). A assinatura é o CÓDIGO do erro + um
        trecho da mensagem do próprio SGBD — nada do nosso benchmark — então no modo reativo o gatilho é o
        engine, não a nossa amostra. As classes vieram dos erros MEDIDOS nos braços aided-MySQL (M3-cru +
        M3-A, 2026-07-19); o contador está no comentário p/ rastreabilidade.

        ⚠️ VIÉS DECLARADO: esta lista cobre o que quebrou NESTAS 21 queries. Um erro de dialeto que nunca
        ocorreu aqui não tem regra — o modo reativo pelo menos não finge que cobre (só dispara no que casa).
        Cobertura de verdade é o degrau 3 (RAG sobre a doc oficial, [1c]). Agnóstico de benchmark: nenhuma
        tabela/coluna/nome de query aparece no texto das regras (testado)."""
        return [
            # 1055 (3×, q51 3/3 — determinístico): ONLY_FULL_GROUP_BY ATIVO nesta instância
            (("1055", "not in group by"),
             "- ONLY_FULL_GROUP_BY is ENABLED here: every column in SELECT/ORDER BY that is not inside an "
             "aggregate MUST appear in GROUP BY. This server will NOT pick an arbitrary row like older "
             "versions did.\n"),
            # 1584 (2×, q40): CAST com tipo do PostgreSQL
            (("1584", "stored function `cast`", "incorrect parameters in the call to stored function"),
             "- CAST accepts ONLY these types here: SIGNED, UNSIGNED, DECIMAL(p,s), CHAR, DATE, DATETIME, "
             "TIME, BINARY. `numeric`, `int`, `float`, `real`, `text`, `bigint` are NOT valid CAST targets, "
             "and there is no `::type` shorthand. For fractional math use CAST(x AS DECIMAL(38,10)).\n"),
            # 1064 (3×): construções de outro dialeto que o parser rejeita
            (("1064", "error in your sql syntax"),
             "- These constructs do NOT exist here — use the stated equivalent:\n"
             "    AGG(...) FILTER (WHERE p)  ->  AGG(CASE WHEN p THEN ... END)\n"
             "    FULL OUTER JOIN            ->  LEFT JOIN  UNION  RIGHT JOIN\n"
             "    a || b  (concatenation)    ->  CONCAT(a, b)\n"
             "    generate_series            ->  an explicit JOIN or derived table\n"
             "    DISTINCT ON                ->  GROUP BY, or a window function with a rank filter\n"),
            # 1176: dica de índice p/ índice inexistente
            (("1176", "key '"),
             "- Never emit index hints (USE INDEX / FORCE INDEX): the index you name may not exist and the "
             "statement then fails. Let the optimizer choose.\n"),
        ]

    def explain(self, query: str, uri: str | None = None) -> dict:
        """Return estimated execution plan via EXPLAIN FORMAT=JSON (no execution)."""
        try:
            engine = get_engine(uri)
            with engine.connect() as conn:
                result = conn.execute(text(f"EXPLAIN FORMAT=JSON {query}"))
                row = result.fetchone()
                plan_str = row[0] if row else "{}"
                plan = json.loads(plan_str) if isinstance(plan_str, str) else plan_str
            return {"type": "mysql", "plan": plan, "execution_time_ms": None, "error": None}
        except Exception as e:
            return {"type": "mysql", "plan": {}, "execution_time_ms": None, "error": str(e)}

    def explain_analyze(self, query: str, timeout_s: int | None = None, uri: str | None = None) -> dict:
        """Execute query with EXPLAIN ANALYZE (MySQL 8.0.18+) and return plan + actual timing.
        `uri` = instância SF1 (verify) p/ o fallback de perf quando o full-scale estoura."""
        try:
            engine = get_engine(uri)
            with engine.connect() as conn:
                timeout_s = timeout_s or int(os.getenv("EXPLAIN_ANALYZE_TIMEOUT_S", "120"))
                # MySQL MAX_EXECUTION_TIME is in milliseconds
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {timeout_s * 1000}"))

                t0 = time.perf_counter()
                result = conn.execute(text(f"EXPLAIN ANALYZE {query}"))
                elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)

                rows = result.fetchall()
                plan_text = "\n".join(str(r[0]) for r in rows)

                # Parse actual engine time from the root node ("actual time=X..Y rows=Z"). H2: track
                # the SOURCE — when the parse misses we fall back to Python wall-clock (parse+network+
                # fetch, NOT engine time), which must be FLAGGABLE so those rows can be excluded from
                # timing analysis instead of silently corrupting improvement_pct.
                parsed_ms = _parse_explain_analyze_time(plan_text)
                execution_time_ms = parsed_ms if parsed_ms is not None else elapsed_ms
                timing_source = "engine" if parsed_ms is not None else "wallclock"

                # Also get JSON plan for cost extraction
                json_plan = self.explain(query, uri=uri)

            return {
                "type": "mysql",
                "plan": json_plan.get("plan", {}),
                "plan_text": plan_text,
                "execution_time_ms": execution_time_ms,
                "timing_source": timing_source,
                "error": None,
            }
        except Exception as e:
            return {"type": "mysql", "plan": {}, "execution_time_ms": None, "timing_source": None, "error": str(e)}

    def hardware_hints(self) -> dict:
        try:
            engine = get_engine()
            with engine.connect() as conn:
                # Buffer pool cache hit ratio
                status = conn.execute(text(
                    "SELECT variable_name, variable_value "
                    "FROM performance_schema.global_status "
                    "WHERE variable_name IN "
                    "('Innodb_buffer_pool_read_requests', 'Innodb_buffer_pool_reads')"
                )).fetchall()
                status_map = {r[0]: int(r[1]) for r in status}
                requests = status_map.get("Innodb_buffer_pool_read_requests", 0)
                disk_reads = status_map.get("Innodb_buffer_pool_reads", 0)
                if requests > 0:
                    cache_hit = round(100.0 * (requests - disk_reads) / requests, 1)
                else:
                    cache_hit = None

                # Sort buffer as work_mem proxy (bytes → MB)
                sort_buf = conn.execute(text("SELECT @@sort_buffer_size")).scalar()
                work_mem_mb = round(int(sort_buf) / (1024 * 1024), 2) if sort_buf else None

                # I/O capacity to infer SSD vs HDD
                io_capacity = conn.execute(text("SELECT @@innodb_io_capacity")).scalar()
                # High io_capacity (≥ 1000) typically means SSD configuration
                if io_capacity and int(io_capacity) >= 1000:
                    random_page_cost, seq_page_cost = 1.0, 1.0
                else:
                    random_page_cost, seq_page_cost = 4.0, 1.0

            return {
                "cache_hit_ratio_pct": cache_hit,
                "work_mem_mb": work_mem_mb,
                "random_page_cost": random_page_cost,
                "seq_page_cost": seq_page_cost,
            }
        except Exception:
            return {}

    def top_node_actual_rows(self, explain_result: dict) -> int | None:
        """H1: ACTUAL output rows from the EXPLAIN ANALYZE root node ('actual time=.. rows=Z', loops=1),
        so the S=1 exact-cardinality cross-check runs on MySQL too. The base returns None → the gate was
        a NO-OP on MySQL, leaving only the LIMIT-100 sample hash (weaker than PG). The root is the first
        timed line in plan_text."""
        plan_text = explain_result.get("plan_text") or ""
        for line in plan_text.splitlines():
            m = re.search(r"actual time=[\d.]+\.\.[\d.]+ rows=(\d+)", line)
            if m:
                return int(m.group(1))
        return None

    def _nonsargable_remedy(self) -> str:  # H4: MySQL-specific — never leak PG's trigram/GIN here
        return "a FULLTEXT index"

    def get_total_cost(self, explain_result: dict) -> float | None:
        """Total cost from MySQL EXPLAIN FORMAT=JSON. H3: the OUTER query_block.query_cost can OMIT the
        cost of materialized CTEs/derived tables (nested query_blocks) — exactly the decorrelation/
        consolidation rewrites we study, which move work INTO a CTE. Walk the WHOLE tree and return the
        MAX query_cost found, so the reported cost is never understated below its most expensive
        sub-block (else the cost gate waves a moved-work rewrite through). MAX, not sum → no double-count
        of costs the outer block already includes."""
        try:
            plan = explain_result.get("plan") or {}
            costs = _collect_mysql_costs(plan.get("query_block") or {})
            return max(costs) if costs else None
        except (TypeError, ValueError, AttributeError):
            return None

    def extract_table_estimates(self, explain_result: dict) -> dict[str, int]:
        """Walk MySQL EXPLAIN JSON and extract estimated rows per table alias."""
        try:
            plan = explain_result.get("plan", {})
            return _walk_mysql_plan(plan.get("query_block", {}))
        except Exception:
            return {}

    def sample_hash(self, query: str, timeout_s: int | None = None, uri: str | None = None) -> tuple[str | None, str | None]:
        stripped = query.strip().rstrip(";").strip()
        # Wrap as a derived table and re-sample deterministically. A subquery may itself
        # contain WITH / ORDER BY / LIMIT (valid in MySQL 8), so this handles CTE and
        # window-function queries too — avoids the fragile regex that truncated an ORDER BY
        # inside an OVER() (bug exposed in TPC-DS q51/q67).
        # `uri` targets a non-primary DB (the smaller-scale verification instance) when the
        # full-scale original is too slow to hash — see executor S=1 fallback.
        try:
            engine = get_engine(uri)
            with engine.connect() as conn:
                timeout_s = timeout_s or int(os.getenv("SAMPLE_HASH_TIMEOUT_S", "120"))
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {timeout_s * 1000}"))
                result = conn.execute(text(self._ordered_sample_sql(conn, stripped, 100)))  # M1: ORDER BY all cols
                rows = sorted(str(row) for row in result.fetchall())
                return hashlib.md5(json.dumps(rows).encode()).hexdigest(), None
        except Exception as e:
            return None, str(e)

    def sample_rows(self, query: str, n: int = 20, timeout_s: int | None = None, uri: str | None = None) -> tuple[list[str] | None, str | None]:
        """Sorted sample rows (as strings) for the rich-S≠1 divergence diff — same derived-table sampling
        as sample_hash, but returns the rows instead of their hash. (rows, None) or (None, error)."""
        stripped = query.strip().rstrip(";").strip()
        try:
            engine = get_engine(uri)
            with engine.connect() as conn:
                timeout_s = timeout_s or int(os.getenv("SAMPLE_HASH_TIMEOUT_S", "120"))
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {timeout_s * 1000}"))
                result = conn.execute(text(self._ordered_sample_sql(conn, stripped, int(n))))  # M1
                return sorted(str(row) for row in result.fetchall()), None
        except Exception as e:
            return None, str(e)

    def get_partial_index_predicates(self, table_name: str) -> dict[str, str]:
        # MySQL does not support PostgreSQL-style partial indexes with WHERE predicates.
        return {}

    def extract_schema(self) -> tuple[list[dict], list[dict]]:
        from sqlalchemy import inspect, text
        engine = get_engine()
        inspector = inspect(engine)
        tables = []
        relations = []

        for table_name in inspector.get_table_names():
            pk = inspector.get_pk_constraint(table_name)
            pk_cols = set(pk.get("constrained_columns", []))

            fks = inspector.get_foreign_keys(table_name)
            fk_map = {}
            for fk in fks:
                for local_col, ref_col in zip(fk["constrained_columns"], fk["referred_columns"]):
                    fk_map[local_col] = {"table": fk["referred_table"], "column": ref_col}
                relations.append({
                    "from_table": table_name,
                    "from_columns": fk["constrained_columns"],
                    "to_table": fk["referred_table"],
                    "to_columns": fk["referred_columns"],
                })

            columns = []
            for c in inspector.get_columns(table_name):
                col = {"name": c["name"], "type": str(c["type"]), "nullable": c.get("nullable", True)}
                if c["name"] in pk_cols:
                    col["primary_key"] = True
                if c["name"] in fk_map:
                    col["foreign_key"] = f"{fk_map[c['name']]['table']}.{fk_map[c['name']]['column']}"
                columns.append(col)

            indexes = []
            for idx in inspector.get_indexes(table_name):
                indexes.append({
                    "name": idx["name"],
                    "columns": idx["column_names"],
                    "unique": idx["unique"],
                })

            unique_constraints = [
                {"name": uc["name"], "columns": uc["column_names"]}
                for uc in inspector.get_unique_constraints(table_name)
            ]

            with engine.connect() as conn:
                row_count = conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar()

            tables.append({
                "name": table_name,
                "row_count": row_count,
                "columns": columns,
                "primary_key": list(pk_cols),
                "indexes": indexes,
                "unique_constraints": unique_constraints,
            })

        return tables, relations

    def _measured_exec_ms(self, conn, sql: str, reps: int = 3) -> float | None:
        """Tempo de EXECUÇÃO medido de `sql` numa conexão ABERTA — aquecimento descartado, mediana de N.

        POR QUE EXISTE (02/09). O eixo de índice do MySQL reportava o **custo do planner** enquanto o
        eixo de reescrita reporta **tempo medido por execução em escala real**. Era inconsistência que
        um revisor lê numa sentada — e há prova medida de que a estimativa engana: no 29c o modelo de
        custo previu regressão de +3,46% onde o tempo real regrediu **+29,4%** (direção certa,
        magnitude errada por ~10x). O `postgres.py` já media assim desde 18/08; o MySQL não, e por isso
        gravava `validation: "estimated"` — o que fazia o canário RECUSAR a célula C4 (correto: a
        recomendação repousaria sobre uma estimativa).

        ⚠️ Precisa rodar na conexão DO CHAMADOR: o índice candidato só existe enquanto o
        `simulate_index` o mantém criado. Abrir uma segunda conexão mediria o banco em outro estado —
        no MySQL o DDL dá commit implícito, então o índice é visível a todos, mas o `finally` do
        chamador pode tê-lo derrubado. Medir aqui dentro é o que garante o pareamento.

        ⛔ `max_execution_time` é OBRIGATÓRIO (lição do #orfãos, 24/08 no PostgreSQL): este caminho roda
        1 aquecimento + N repetições por candidato a índice. Sem limite, uma medição abandonada
        continua executando depois que o cliente desiste, e essas queries órfãs competem por CPU com a
        célula viva — e tempo de execução É a medição. O orçamento é generoso de propósito (o ponto é
        MEDIR, não cortar cedo) mas finito.
        ⚠️ O `max_execution_time` do MySQL vale só para SELECT e é em MILISSEGUNDOS.
        """
        import statistics
        budget_ms = int(os.getenv("INDEX_MEASURE_TIMEOUT_S", "180")) * 1000
        try:
            conn.execute(text(f"SET SESSION max_execution_time = {budget_ms}"))
            conn.execute(text(sql)).fetchall()          # aquecimento, descartado
            times = []
            for _ in range(max(1, reps)):
                t0 = time.perf_counter()
                conn.execute(text(sql)).fetchall()
                times.append((time.perf_counter() - t0) * 1000)
            return round(statistics.median(times), 3) if times else None
        except Exception as e:
            log.warning(f"[simulate_index] medicao por execucao falhou: {e}")
            return None
        finally:
            try:
                conn.execute(text("SET SESSION max_execution_time = 0"))
            except Exception:
                pass

    def ping_latency(self) -> float:
        engine = get_engine()
        with engine.connect() as conn:
            t0 = time.perf_counter()
            conn.execute(text("SELECT 1"))
            return round((time.perf_counter() - t0) * 1000, 2)

    def db_version(self) -> str:
        try:
            engine = get_engine()
            with engine.connect() as conn:
                version = conn.execute(text("SELECT VERSION()")).scalar()
            return f"MySQL {version}"
        except Exception:
            return ""

    def is_timeout_error(self, error_message: str) -> bool:
        return "maximum statement execution time exceeded" in error_message.lower()

    def schema_fingerprint(self) -> str | None:
        try:
            engine = get_engine()
            with engine.connect() as conn:
                result = conn.execute(text(
                    "SELECT MD5(GROUP_CONCAT(INDEX_NAME, TABLE_NAME ORDER BY INDEX_NAME SEPARATOR ',')) "
                    "FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = DATABASE()"
                )).scalar()
            return result
        except Exception:
            return None

    def run_analyze(self, tables: list[str]) -> None:
        engine = get_engine()
        with engine.connect() as conn:
            for table in tables:
                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_$]*$', table):
                    conn.execute(text(f"ANALYZE TABLE `{table}`"))

    def generate_index_ddl(self, spec: dict) -> str:
        table = spec["target"]
        column = spec["property"]
        index_type = spec.get("index_type", "btree")
        prefix_length = spec.get("prefix_length")
        idx_name = f"idx_{table}_{column}_{index_type}"

        col_expr = f"{column}({prefix_length})" if prefix_length else column

        if index_type == "fulltext":
            return f"CREATE FULLTEXT INDEX {idx_name} ON `{table}` ({col_expr})"
        if index_type == "hash":
            # HASH indexes are engine-specific in MySQL (MEMORY/NDB); btree is the safe default
            return f"CREATE INDEX {idx_name} ON `{table}` ({col_expr}) USING HASH"
        # btree (default)
        return f"CREATE INDEX {idx_name} ON `{table}` ({col_expr})"

    def _table_write_stats(self, table: str) -> dict:
        """Read actual write volume and row count from performance_schema."""
        try:
            engine = get_engine()
            with engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT
                        COALESCE(t.COUNT_INSERT, 0) + COALESCE(t.COUNT_UPDATE, 0) + COALESCE(t.COUNT_DELETE, 0),
                        COALESCE(t.COUNT_UPDATE, 0) + COALESCE(t.COUNT_DELETE, 0),
                        COALESCE(i.TABLE_ROWS, 0)
                    FROM performance_schema.table_io_waits_summary_by_table t
                    JOIN information_schema.TABLES i
                        ON t.OBJECT_NAME = i.TABLE_NAME
                        AND t.OBJECT_SCHEMA = i.TABLE_SCHEMA
                    WHERE t.OBJECT_SCHEMA = DATABASE()
                    AND t.OBJECT_NAME = :table
                """), {"table": table}).fetchone()
                if row:
                    return {
                        "writes_total": int(row[0]),
                        "writes_mutating": int(row[1]),
                        "live_rows": int(row[2]),
                    }
        except Exception:
            pass
        return {}

    def existing_index_covering(self, table: str, columns: list[str], index_type: str) -> str | None:
        """Return the name of an existing index that already covers these columns
        (leftmost-prefix match, same index type), or None."""
        try:
            engine = get_engine()
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT INDEX_NAME, INDEX_TYPE, COLUMN_NAME "
                    "FROM information_schema.statistics "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table "
                    "ORDER BY INDEX_NAME, SEQ_IN_INDEX"
                ), {"table": table}).fetchall()
            wanted = [c.strip().lower() for c in columns if c.strip()]
            if not wanted:
                return None
            method_wanted = (index_type or "btree").strip().lower()
            indexes: dict[str, list[str]] = {}
            methods: dict[str, str] = {}
            for name, itype, col in rows:
                indexes.setdefault(name, []).append((col or "").lower())
                methods[name] = (itype or "").lower()
            for name, cols in indexes.items():
                if methods.get(name) != method_wanted:
                    continue
                if cols[:len(wanted)] == wanted:
                    return name
        except Exception:
            return None
        return None

    def _explain_cost_on_conn(self, conn, sql: str) -> float | None:
        """EXPLAIN FORMAT=JSON on an existing connection (used while the temp index exists)."""
        row = conn.execute(text(f"EXPLAIN FORMAT=JSON {sql}")).fetchone()
        plan_str = row[0] if row else "{}"
        plan = json.loads(plan_str) if isinstance(plan_str, str) else plan_str
        return self.get_total_cost({"type": "mysql", "plan": plan, "execution_time_ms": None, "error": None})

    def simulate_index(self, spec: dict, query: str, workload: list[dict] | None = None) -> dict:
        # MySQL has no hypothetical index tool, so we simulate via CREATE → EXPLAIN → DROP.
        # MySQL DDL (CREATE/DROP INDEX) causes an implicit commit, so we use AUTOCOMMIT mode.
        # The index is always dropped in a finally block — safe for static benchmark databases.
        _TYPE_WRITE_MULTIPLIER = {"fulltext": 3.0, "btree": 1.0, "hash": 1.0}

        ddl = spec.get("ddl") or self.generate_index_ddl(spec)
        index_type = spec.get("index_type", "btree").lower()
        type_multiplier = _TYPE_WRITE_MULTIPLIER.get(index_type, 1.0)

        write_stats = self._table_write_stats(spec["target"])
        live_rows = write_stats.get("live_rows", 0)
        writes_mutating = write_stats.get("writes_mutating", 0)
        write_intensity = writes_mutating / max(live_rows, 1)
        write_overhead_pct = round(min(write_intensity * type_multiplier * 100, 100.0), 1)

        original_explain = self.explain(query)
        original_cost = self.get_total_cost(original_explain)

        # Workload-aware: baseline cost of every other registry query touching this table
        workload_baselines = self._workload_baselines(workload)

        table = spec["target"]
        # LLM sometimes wraps composite column lists in parentheses: "(col1, col2)" → strip them
        property_col = spec.get("property", "").strip("() ")
        prefix_length = spec.get("prefix_length")

        # Unique temp name — _athena_sim_ prefix, sanitized, truncated to MySQL's 64-char limit
        raw_name = f"_athena_sim_{table}_{re.sub(r'[,\s]+', '_', property_col)}"
        sim_index_name = raw_name[:64]

        cols = [c.strip() for c in property_col.split(",")]
        col_def = ", ".join(
            f"`{c}`({prefix_length})" if prefix_length else f"`{c}`"
            for c in cols
        )
        sim_ddl = f"CREATE INDEX `{sim_index_name}` ON `{table}` ({col_def})"
        drop_ddl = f"DROP INDEX `{sim_index_name}` ON `{table}`"

        optimized_cost = None
        inner_error = None
        workload_costs: dict = {}
        # ⭐ tempos medidos por execução (só preenchidos com INDEX_VALIDATION=executed)
        baseline_ms = None
        optimized_ms = None
        engine = get_engine()

        def _error_result(msg: str) -> dict:
            return {
                "table": table,
                "column": property_col,
                "index_type": index_type,
                "ddl": ddl,
                "estimated_benefit": spec.get("estimated_benefit", ""),
                "original_plan_cost": original_cost,
                "optimized_plan_cost": None,
                "estimated_read_benefit_pct": None,
                "write_overhead_pct": write_overhead_pct,
                "index_size_bytes": None,
                "table_writes_total": write_stats.get("writes_total"),
                "table_live_rows": live_rows,
                "recommendation": msg,
            }

        try:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                # Drop ALL _athena_sim_ orphans on this table — covers name changes between code versions
                try:
                    orphans = conn.execute(text(
                        "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :tbl "
                        "AND INDEX_NAME LIKE '\\_athena\\_sim\\_%'"
                    ), {"tbl": table}).fetchall()
                    for row in orphans:
                        try:
                            conn.execute(text(f"DROP INDEX `{row[0]}` ON `{table}`"))
                        except Exception:
                            pass
                except Exception:
                    pass

                try:
                    conn.execute(text(sim_ddl))
                except Exception as e:
                    return _error_result(f"simulation failed — could not create index: {e}")

                try:
                    result = conn.execute(text(f"EXPLAIN FORMAT=JSON {query}"))
                    row = result.fetchone()
                    plan_str = row[0] if row else "{}"
                    plan = json.loads(plan_str) if isinstance(plan_str, str) else plan_str
                    optimized_cost = self.get_total_cost(
                        {"type": "mysql", "plan": plan, "execution_time_ms": None, "error": None}
                    )
                    # Workload loop — temp index still physically present. The expensive
                    # CREATE is paid once; each extra EXPLAIN costs milliseconds.
                    for b in workload_baselines:
                        try:
                            workload_costs[b["source"]] = self._explain_cost_on_conn(conn, b["sql"])
                        except Exception:
                            workload_costs[b["source"]] = None

                    # ⭐ MEDIÇÃO POR EXECUÇÃO (02/09) — o índice temporário ainda está fisicamente
                    # criado aqui, então este é o ÚNICO ponto em que dá para medir "com índice".
                    # O "sem índice" é medido depois, fora do bloco, com o índice já derrubado.
                    # ⚠️ O CREATE INDEX já foi pago acima: antes desta mudança o MySQL pagava o custo
                    # de CONSTRUIR o índice e mesmo assim só perguntava o custo ao planner.
                    if _index_validation_executed():
                        optimized_ms = self._measured_exec_ms(conn, query)
                except Exception as e:
                    inner_error = e
                finally:
                    try:
                        conn.execute(text(drop_ddl))
                    except Exception as drop_e:
                        if "foreign key" in str(drop_e).lower():
                            # MySQL adopted our temp index to enforce a FK constraint.
                            # Create a permanent replacement so MySQL releases the orphan.
                            try:
                                perm_name = f"idx_{table}_{re.sub(r'[,\s]+', '_', property_col)}_fk_rescue"[:64]
                                conn.execute(text(f"CREATE INDEX `{perm_name}` ON `{table}` ({col_def})"))
                                conn.execute(text(drop_ddl))
                            except Exception as rescue_e:
                                log.warning(f"[simulate_index] FK rescue failed for {sim_index_name}: {rescue_e}")
                        else:
                            log.warning(f"[simulate_index] failed to drop temp index {sim_index_name}: {drop_e}")

        except Exception as e:
            return _error_result(f"simulation failed — {e}")

        if inner_error:
            return _error_result(f"simulation failed — {inner_error}")

        # ⭐ BASELINE por execução — medida DEPOIS, com o índice temporário já derrubado.
        # ⚠️ A ordem importa e é o oposto da intuição: no PostgreSQL o índice vive dentro de uma
        # transação e some no ROLLBACK, então lá dá para medir os dois lados na mesma sessão. No MySQL
        # o DDL dá COMMIT IMPLÍCITO — o índice fica visível ao banco inteiro até o DROP. Logo o
        # "sem índice" só é mensurável aqui, depois do `finally` que o derruba.
        # ⛔ Só medir a baseline se o lado "com índice" foi medido: um par incompleto não vira número.
        if optimized_ms is not None:
            try:
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn2:
                    baseline_ms = self._measured_exec_ms(conn2, query)
            except Exception as e:
                log.warning(f"[simulate_index] baseline por execucao falhou: {e}")

        benefit_pct = None
        if original_cost is not None and optimized_cost is not None and original_cost > 0:
            benefit_pct = round(((original_cost - optimized_cost) / original_cost) * 100, 1)

        # MEDIDO — o número que o paper deve reportar quando INDEX_VALIDATION=executed. Fica SEPARADO
        # do benefit_pct (a estimativa do planner) para que os dois viajem no registro e a distância
        # entre eles possa ser reportada, em vez de silenciosamente resolvida.
        measured_benefit_pct = None
        if baseline_ms and optimized_ms and baseline_ms > 0:
            measured_benefit_pct = round(((baseline_ms - optimized_ms) / baseline_ms) * 100, 1)

        # ⭐ Quando existe número MEDIDO, a RECOMENDAÇÃO é feita sobre ele — não sobre a estimativa.
        # Era exatamente esta a inconsistência que travava o C4: recomendar índice no MySQL a partir do
        # custo do planner, enquanto o eixo de reescrita decide por tempo executado.
        decide_pct = measured_benefit_pct if measured_benefit_pct is not None else benefit_pct
        how = ("MEASURED by execution (warm-up + median of 3, index built for real then dropped)"
               if measured_benefit_pct is not None
               else "planner cost ESTIMATE (real DDL, dropped)")
        if decide_pct is None:
            rec = "simulation incomplete — cost comparison unavailable"
        elif decide_pct > write_overhead_pct:
            rec = (f"beneficial — read gain ({decide_pct:.1f}%) outweighs write overhead "
                   f"({write_overhead_pct:.1f}%) [{how}]")
        else:
            rec = (f"not recommended — read gain ({decide_pct:.1f}%) does not justify write overhead "
                   f"({write_overhead_pct:.1f}%) [{how}]")

        workload_impact = self._build_workload_impact(workload_baselines, workload_costs)
        rec = self._append_workload_note(rec, workload_impact)

        return {
            "table": table,
            "column": property_col,
            "index_type": index_type,
            "ddl": ddl,
            "estimated_benefit": spec.get("estimated_benefit", ""),
            "original_plan_cost": original_cost,
            "optimized_plan_cost": optimized_cost,
            "estimated_read_benefit_pct": benefit_pct,
            # Os DOIS números viajam juntos de propósito: `estimated_read_benefit_pct` é o que o
            # planner prevê, `measured_read_benefit_pct` é o que a execução mostra. Reportar só o
            # primeiro é a inconsistência que travou o C4; reportar só o segundo joga fora a
            # comparação que dá sentido ao ponto.
            "baseline_exec_ms": baseline_ms,
            "optimized_exec_ms": optimized_ms,
            "measured_read_benefit_pct": measured_benefit_pct,
            # ⭐ O campo que o canário lê (`canary_check.py::_index_validation`). Antes ele nunca era
            # escrito aqui, e o `suggestion_store` caía no default "estimated" — foi isso que fez o
            # canário RECUSAR o C4 duas vezes. A recusa estava certa; faltava o backend medir.
            "validation": "executed" if measured_benefit_pct is not None else "estimated",
            "write_overhead_pct": write_overhead_pct,
            "index_size_bytes": None,
            "table_writes_total": write_stats.get("writes_total"),
            "table_live_rows": live_rows,
            "recommendation": rec,
            "workload_impact": workload_impact,
        }

    # cost_gate_error: the "real time improved → cost is unreliable" suppression now lives
    # in the base class (SQLBackend), since both MySQL and PostgreSQL suffer the CTE
    # cardinality-estimation problem. MySQL's temp-table materialization (no column stats)
    # is the MySQL-specific manifestation; no separate override needed.

    def build_index_advisor_prompt(self, query: str, explain: dict, schema_context: dict, query_tables: set[str], suggestions: list[str] | None = None, workload_digest: str | None = None) -> str:
        import json
        db_section = f"Database: {self.db_version()}\n" if self.db_version() else ""
        schema_section = self._format_schema_for_index_prompt(schema_context, query_tables)
        plan_json = (
            json.dumps(explain.get("plan", {}), indent=2)
            if not explain.get("error") else f"Error: {explain['error']}"
        )
        optimizer_hints_section = ""
        if suggestions:
            hints = "\n".join(f"- {s}" for s in suggestions)
            optimizer_hints_section = f"\n=== OPTIMIZER HINTS (from SQL architect) ===\n{hints}\n"
        # WORKLOAD-AWARE generation (the second form): the model sees the OTHER queries sharing these
        # tables and is told to favor columns that speed up MANY of them, not just this one.
        workload_section = f"\n{workload_digest}\n" if workload_digest else ""
        return f"""You are a database index advisor. A SQL query has been analyzed and cannot be improved through SQL rewriting alone.
Your task: identify the index(es) that would most reduce execution cost for this specific query.

{db_section}
=== QUERY ===
{query}

=== SCHEMA FOR RELEVANT TABLES (with existing indexes) ===
{schema_section}

=== PHYSICAL EXECUTION PLAN ===
{plan_json}
{optimizer_hints_section}{workload_section}
Instructions:
1. Find full table scans (access_type: "ALL" in the plan) on large tables — these are the primary index candidates.
2. Identify the column causing the scan: the WHERE filter or JOIN condition on that table.
3. Decide the appropriate index type for that column and access pattern.
4. For TEXT or BLOB columns, a prefix length is required in MySQL — set prefix_length to the appropriate value (e.g. 255).
5. MySQL does not support partial indexes — always set partial_predicate to null.
6. Do NOT suggest indexes that already exist on the table.
7. If there is no access_type "ALL" in the plan, set no_index_possible to true.
8. Use the OPTIMIZER HINTS section (if present) as additional signal — the SQL architect identified these as potential missing indexes.

Return ONLY this JSON structure — no explanation outside the JSON:
{{
  "indexes": [
    {{
      "target": "<table_name>",
      "property": "<column_name>",
      "index_type": "<index type>",
      "prefix_length": null,
      "partial_predicate": null,
      "estimated_benefit": "<what full table scan this eliminates and on how many rows>"
    }}
  ],
  "no_index_possible": false,
  "explanation": "<why these indexes were chosen, or why no index is possible>"
}}\""""


def _parse_explain_analyze_time(plan_text: str) -> float | None:
    """Extract actual end-time (ms) from the root node of EXPLAIN ANALYZE output.

    MySQL 8.0 EXPLAIN ANALYZE format:
        -> Aggregate: ...  (actual time=0.123..456.789 rows=1 loops=1)
    The first line is the root node; its end-time is the total query execution time.
    """
    # Scan for the FIRST line bearing an actual-time (the root/outermost node). splitlines()[0] alone
    # missed whenever a leading blank/wrapper line preceded the root → silent wall-clock fallback (H2).
    for line in plan_text.splitlines():
        match = re.search(r"actual time=[\d.]+\.\.([\d.]+)", line)
        if match:
            return float(match.group(1))
    return None


def _collect_mysql_costs(node) -> list[float]:
    """H3: every `cost_info.query_cost` anywhere in a MySQL EXPLAIN JSON subtree — including nested
    CTE/derived/subquery query_blocks the OUTER query_cost may omit. (Table-level cost_info has no
    `query_cost` key, so only query_block-level costs are collected.)"""
    out: list[float] = []
    if isinstance(node, dict):
        ci = node.get("cost_info")
        if isinstance(ci, dict) and ci.get("query_cost") is not None:
            try:
                out.append(float(ci["query_cost"]))
            except (TypeError, ValueError):
                pass
        for v in node.values():
            out.extend(_collect_mysql_costs(v))
    elif isinstance(node, list):
        for item in node:
            out.extend(_collect_mysql_costs(item))
    return out


def _walk_mysql_plan(node: dict) -> dict[str, int]:
    """Recursively extract (alias → estimated_rows) from MySQL EXPLAIN FORMAT=JSON."""
    result = {}
    if "table" in node:
        tbl = node["table"]
        alias = tbl.get("alias") or tbl.get("table_name")
        rows = tbl.get("rows_examined_per_scan", 0)
        filtered = float(tbl.get("filtered", "100").replace("%", "")) / 100.0
        if alias:
            result[alias.lower()] = int(rows * filtered)

    for key in ("nested_loop", "ordering_operation", "grouping_operation",
                "duplicates_removal", "windowing", "union_result"):
        sub = node.get(key)
        if isinstance(sub, list):
            for item in sub:
                result.update(_walk_mysql_plan(item))
        elif isinstance(sub, dict):
            result.update(_walk_mysql_plan(sub))

    return result
