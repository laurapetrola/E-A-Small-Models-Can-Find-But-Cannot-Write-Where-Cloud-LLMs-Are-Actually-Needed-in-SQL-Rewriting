import hashlib
import json
import os
import re

from sqlalchemy import text

from src.connections import get_engine
from src.pipeline.backends.relational.sql import SQLBackend


def _parse_work_mem_mb(setting: str) -> float:
    s = setting.strip().upper()
    if s.endswith("GB"):
        return float(s[:-2]) * 1024
    if s.endswith("MB"):
        return float(s[:-2])
    if s.endswith("KB"):
        return float(s[:-2]) / 1024
    return float(s) / (1024 * 1024)


def _index_validation_executed() -> bool:
    """INDEX_VALIDATION=executed measures the candidate index by EXECUTION instead of trusting the
    planner's cost estimate.

    Default stays `simulated` so every cell measured before 2026-08-18 keeps its exact meaning — the
    two regimes are NOT comparable and must never be pooled in one table.
    """
    return os.getenv("INDEX_VALIDATION", "simulated").strip().lower() in ("executed", "execute", "real", "measured")


class PostgresBackend(SQLBackend):
    """PostgreSQL-specific backend."""

    def sqlglot_dialect(self) -> str:
        return "postgres"

    def syntax_hint(self) -> str:
        return (
            "Syntax rules for PostgreSQL:\n"
            "- Structure: SELECT ... FROM table1 JOIN table2 ON condition ... WHERE filters\n"
            "- JOIN clauses always come before WHERE — never after\n"
            "- Each JOIN must have an ON clause\n"
            "- No trailing semicolon\n"
            "- Subqueries must be aliased: (SELECT ...) AS alias"
        )

    def explain(self, query: str, uri: str | None = None) -> dict:
        return self._run_explain(query, analyze=False, uri=uri)

    def explain_analyze(self, query: str, timeout_s: int | None = None, uri: str | None = None) -> dict:
        return self._run_explain(query, analyze=True, timeout_s=timeout_s, uri=uri)

    def hardware_hints(self) -> dict:
        try:
            engine = get_engine()
            with engine.connect() as conn:
                cache_hit = conn.execute(text(
                    "SELECT round(100.0 * blks_hit / NULLIF(blks_hit + blks_read, 0), 1) "
                    "FROM pg_stat_database WHERE datname = current_database()"
                )).scalar()
                work_mem = conn.execute(text("SELECT current_setting('work_mem')")).scalar()
                random_page_cost = conn.execute(text("SELECT current_setting('random_page_cost')::float")).scalar()
                seq_page_cost = conn.execute(text("SELECT current_setting('seq_page_cost')::float")).scalar()
            return {
                "cache_hit_ratio_pct": float(cache_hit) if cache_hit is not None else None,
                "work_mem_mb": _parse_work_mem_mb(work_mem),
                "random_page_cost": float(random_page_cost),
                "seq_page_cost": float(seq_page_cost),
            }
        except Exception:
            return {}

    def get_total_cost(self, explain_result: dict) -> float | None:
        try:
            plan = explain_result["plan"]
            if isinstance(plan, list):
                return float(plan[0]["Plan"]["Total Cost"])
        except (KeyError, IndexError, TypeError):
            pass
        return None

    def top_node_actual_rows(self, explain_result: dict) -> int | None:
        """The root plan node's "Actual Rows" (present only under EXPLAIN ANALYZE) = true output
        cardinality. None if absent (plain EXPLAIN, timed-out original, or malformed plan)."""
        try:
            plan = explain_result["plan"]
            if isinstance(plan, list) and plan:
                rows = plan[0]["Plan"].get("Actual Rows")
                return int(rows) if rows is not None else None
        except (KeyError, IndexError, TypeError, ValueError):
            pass
        return None

    def extract_table_estimates(self, explain_result: dict) -> dict[str, int]:
        def _walk(node: dict) -> dict[str, int]:
            result = {}
            if "Relation Name" in node and "Alias" in node:
                result[node["Alias"].lower()] = int(node.get("Plan Rows", 0))
            for child in node.get("Plans", []):
                result.update(_walk(child))
            return result

        try:
            plan = explain_result.get("plan", [])
            if isinstance(plan, list) and plan:
                return _walk(plan[0]["Plan"])
        except Exception:
            pass
        return {}

    def _nonsargable_remedy(self) -> str:  # H4: PG-specific — never sent to the MySQL model
        return "a trigram/GIN index"

    def sample_hash(self, query: str, timeout_s: int | None = None, uri: str | None = None) -> tuple[str | None, str | None]:
        stripped = query.strip().rstrip(";").strip()
        # Wrap as a derived table and re-sample deterministically. A subquery may itself
        # contain WITH / ORDER BY / LIMIT (valid in PG and MySQL 8), so this handles CTE and
        # window-function queries too — avoids the fragile regex that truncated an ORDER BY
        # inside an OVER() (bug exposed in TPC-DS q51/q67).
        # `uri` targets a non-primary DB (the smaller-scale verification instance) when the
        # full-scale original is too slow to hash — see executor S=1 fallback.
        try:
            engine = get_engine(uri)
            with engine.connect() as conn:
                budget = timeout_s or int(os.getenv("SAMPLE_HASH_TIMEOUT_S", "120"))
                conn.execute(text(f"SET statement_timeout = '{budget}s'"))
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
                budget = timeout_s or int(os.getenv("SAMPLE_HASH_TIMEOUT_S", "120"))
                conn.execute(text(f"SET statement_timeout = '{budget}s'"))
                result = conn.execute(text(self._ordered_sample_sql(conn, stripped, int(n))))  # M1
                return sorted(str(row) for row in result.fetchall()), None
        except Exception as e:
            return None, str(e)

    def get_partial_index_predicates(self, table_name: str) -> dict[str, str]:
        result = {}
        try:
            engine = get_engine()
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t AND indexdef LIKE '%WHERE%'"),
                    {"t": table_name},
                ).fetchall()
                for row in rows:
                    match = re.search(r"\bWHERE\b(.+)$", row[1], re.IGNORECASE)
                    if match:
                        result[row[0]] = match.group(1).strip()
        except Exception:
            pass
        return result

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

            partial_index_map = self.get_partial_index_predicates(table_name)

            indexes = []
            for idx in inspector.get_indexes(table_name):
                entry = {"name": idx["name"], "columns": idx["column_names"], "unique": idx["unique"]}
                if idx["name"] in partial_index_map:
                    entry["predicate"] = partial_index_map[idx["name"]]
                indexes.append(entry)

            unique_constraints = [
                {"name": uc["name"], "columns": uc["column_names"]}
                for uc in inspector.get_unique_constraints(table_name)
            ]

            with engine.connect() as conn:
                row_count = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()

            tables.append({
                "name": table_name,
                "row_count": row_count,
                "columns": columns,
                "primary_key": list(pk_cols),
                "indexes": indexes,
                "unique_constraints": unique_constraints,
            })

        return tables, relations

    def ping_latency(self) -> float:
        import time
        engine = get_engine()
        with engine.connect() as conn:
            start = time.perf_counter()
            conn.execute(text("SELECT 1"))
            return round((time.perf_counter() - start) * 1000, 2)

    def db_version(self) -> str:
        try:
            engine = get_engine()
            with engine.connect() as conn:
                version = conn.execute(text("SHOW server_version")).scalar()
                exts = conn.execute(text(
                    "SELECT name FROM pg_available_extensions "
                    "WHERE installed_version IS NOT NULL"
                )).fetchall()
                _INDEX_RELEVANT = {
                    "pg_trgm", "pg_bigm", "rum",
                    "btree_gin", "btree_gist", "bloom",
                    "hypopg", "pg_stat_statements",
                }
                exts = [r for r in exts if r[0] in _INDEX_RELEVANT]
            parts = [f"PostgreSQL {version}"]
            if exts:
                parts.append(f"extensions: {', '.join(sorted(r[0] for r in exts))}")
            return " | ".join(parts)
        except Exception:
            return ""

    def build_index_advisor_prompt(self, query: str, explain: dict, schema_context: dict, query_tables: set[str], suggestions: list[str] | None = None, workload_digest: str | None = None) -> str:
        import json
        db_section = f"Database: {self.db_version()}\n" if self.db_version() else ""
        schema_section = self._format_schema_for_index_prompt(schema_context, query_tables)
        plan_json = (
            json.dumps(explain.get("plan", {}), indent=2)
            if not explain.get("error") else f"Error: {explain['error']}"
        )
        suggestions_section = ""
        if suggestions:
            suggestions_section = "\n=== OPTIMIZER HINTS (from query analysis) ===\n" + "\n".join(f"- {s}" for s in suggestions) + "\n"
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
{suggestions_section}{workload_section}
Instructions:
1. Find Seq Scans on large tables — these are the primary index candidates.
   Also look inside SubPlan and InitPlan nodes — EXISTS/NOT EXISTS subqueries execute as
   correlated subplans and their Seq Scans are often the real bottleneck.
2. For EXISTS/NOT EXISTS in the SQL, identify the join condition columns inside them
   (e.g. WHERE outer.id = inner.fk) — those inner columns are strong index candidates.
3. Identify the column causing the scan: the WHERE filter or JOIN condition on that table.
4. Decide the appropriate index type for that column and access pattern. The Database line above lists the installed extensions.
5. If multiple scans exist, order the indexes by the number of rows scanned — largest first.
6. Use the OPTIMIZER HINTS section (if present) as additional signal — they may name columns
   that the execution plan alone does not make obvious.
7. Do NOT suggest indexes that already exist on the table.
8. partial_predicate must reference ONLY columns of the indexed `target` table. A filter on a column
   that comes from a JOINED table cannot be a partial-index predicate on this table — it belongs to a
   different table. When in doubt, set partial_predicate to null.
9. If there is no clear Seq Scan bottleneck, set no_index_possible to true.

Return ONLY this JSON structure — no explanation outside the JSON:
{{
  "indexes": [
    {{
      "target": "<table_name>",
      "property": "<column_name>",
      "index_type": "<index type>",
      "partial_predicate": null,
      "estimated_benefit": "<what Seq Scan this eliminates and on how many rows>"
    }}
  ],
  "no_index_possible": false,
  "explanation": "<why these indexes were chosen, or why no index is possible>"
}}"""

    def run_analyze(self, tables: list[str]) -> None:
        engine = get_engine()
        with engine.connect() as conn:
            for table in tables:
                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_$]*$', table):
                    conn.execute(text(f"ANALYZE {table}"))

    def _column_type(self, table: str, column: str) -> str:
        """Lookup actual data_type from information_schema — drives GIN operator class choice."""
        try:
            engine = get_engine()
            with engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = :t AND column_name = :c LIMIT 1
                """), {"t": table, "c": column}).fetchone()
                return row[0] if row else "unknown"
        except Exception:
            return "unknown"

    def _table_columns(self, table: str) -> set[str]:
        """Lowercased column names of `table` (for partial-predicate validation)."""
        try:
            engine = get_engine()
            with engine.connect() as conn:
                rows = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns WHERE table_name = :t
                """), {"t": table}).fetchall()
                return {r[0].lower() for r in rows}
        except Exception:
            return set()

    def _table_write_stats(self, table: str) -> dict:
        """Read actual write volume and row count from pg_stat_user_tables."""
        try:
            engine = get_engine()
            with engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT
                        COALESCE(n_tup_ins, 0) + COALESCE(n_tup_upd, 0) + COALESCE(n_tup_del, 0),
                        COALESCE(n_tup_upd, 0) + COALESCE(n_tup_del, 0),
                        COALESCE(n_live_tup, 0),
                        COALESCE(n_dead_tup, 0)
                    FROM pg_stat_user_tables WHERE relname = :t
                """), {"t": table}).fetchone()
                if row:
                    return {
                        "writes_total": int(row[0]),
                        "writes_mutating": int(row[1]),
                        "live_rows": int(row[2]),
                        "dead_rows": int(row[3]),
                    }
        except Exception:
            pass
        return {}

    # Explicit operator class per column type — GIN is only valid for these
    _GIN_OP_CLASS: dict[str, str] = {
        "text": "gin_trgm_ops",
        "character varying": "gin_trgm_ops",
        "character": "gin_trgm_ops",
        "jsonb": "jsonb_path_ops",
    }
    # Types where default GIN ops apply (no explicit operator class needed)
    _GIN_DEFAULT_TYPES: frozenset[str] = frozenset({"tsvector", "anyarray"})

    def generate_index_ddl(self, spec: dict) -> str | None:
        table = spec["target"]
        column = spec["property"]
        # Normalize the access method: models spell btree as "b-tree"/"b tree" etc., which would
        # otherwise miss the btree branch and emit an invalid `USING b-tree` (deepseek-r1 q1, 2026-06-15).
        index_type = self._normalize_index_type(spec.get("index_type"))
        partial = self._sanitize_partial_predicate(table, spec.get("partial_predicate"))
        idx_name = re.sub(r'[,\s]+', '_', f"idx_{table}_{column}_{index_type}")
        if index_type == "gin":
            col_type = self._column_type(table, column)
            op_class = self._GIN_OP_CLASS.get(col_type)
            if op_class:
                col_expr = f"{column} {op_class}"
            elif col_type in self._GIN_DEFAULT_TYPES or col_type.endswith("[]"):
                col_expr = column  # arrays and tsvector use default GIN ops
            else:
                return None  # GIN has no valid operator class for this type
            using = " USING gin"
        elif index_type == "btree":
            col_expr = column
            using = ""
        else:
            col_expr = column
            using = f" USING {index_type}"
        where = f" WHERE {partial}" if partial else ""
        return f"CREATE INDEX {idx_name} ON {table}{using} ({col_expr}){where}"

    def existing_index_covering(self, table: str, columns: list[str], index_type: str) -> str | None:
        """Return the name of an existing index that already covers these columns
        (leftmost-prefix match, same access method), or None. Partial indexes are
        skipped — they don't cover general use of the column."""
        try:
            engine = get_engine()
            with engine.connect() as conn:
                rows = conn.execute(text("""
                    SELECT i.relname AS index_name,
                           am.amname AS method,
                           array_agg(a.attname ORDER BY x.ord) AS cols
                    FROM pg_index idx
                    JOIN pg_class i ON i.oid = idx.indexrelid
                    JOIN pg_class t ON t.oid = idx.indrelid
                    JOIN pg_am am ON am.oid = i.relam
                    JOIN LATERAL unnest(idx.indkey) WITH ORDINALITY AS x(attnum, ord) ON true
                    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = x.attnum
                    WHERE t.relname = :table AND idx.indpred IS NULL
                    GROUP BY i.relname, am.amname
                """), {"table": table}).fetchall()
            wanted = [c.strip().lower() for c in columns if c.strip()]
            if not wanted:
                return None
            method_wanted = (index_type or "btree").strip().lower()
            for index_name, method, cols in rows:
                if method.lower() != method_wanted:
                    continue
                if [c.lower() for c in cols[:len(wanted)]] == wanted:
                    return index_name
        except Exception:
            return None
        return None

    @staticmethod
    def _explain_cost_on_conn(conn, sql: str) -> float | None:
        """EXPLAIN on an existing connection (hypopg indexes are session-local)."""
        row = conn.execute(text(f"EXPLAIN (FORMAT JSON) {sql}")).fetchone()
        plan = row[0]
        if isinstance(plan, list) and plan:
            return float(plan[0]["Plan"]["Total Cost"])
        return None

    def simulate_index(self, spec: dict, query: str, workload: list[dict] | None = None) -> dict:
        # Write cost multipliers per index type — algorithmic complexity ratios, not heuristics:
        # btree: O(log n) per write, 1 index page update
        # GIN: O(tokens) per write, multiple posting list updates (~3× btree)
        # brin: O(1) per write, only updates correlation summary
        _TYPE_WRITE_MULTIPLIER = {"gin": 3.0, "btree": 1.0, "hash": 1.0, "brin": 0.1, "gist": 2.0}

        ddl = spec.get("ddl") or self.generate_index_ddl(spec)
        index_type = spec.get("index_type", "btree").lower()
        type_multiplier = _TYPE_WRITE_MULTIPLIER.get(index_type, 1.0)

        # Real write stats from the database
        write_stats = self._table_write_stats(spec["target"])
        live_rows = write_stats.get("live_rows", 0)
        writes_mutating = write_stats.get("writes_mutating", 0)

        # Write overhead derived from real data:
        # turnover = (updates + deletes) / live_rows — how often each row changes
        # overhead ≈ turnover × type_multiplier (GIN touches more index entries per mutation)
        write_intensity = writes_mutating / max(live_rows, 1)
        write_overhead_pct = round(min(write_intensity * type_multiplier * 100, 100.0), 1)

        original_explain = self.explain(query)
        original_cost = self.get_total_cost(original_explain)

        # Workload-aware: baseline cost of every other registry query touching this table,
        # measured against the CURRENT bank state (before the candidate exists).
        workload_baselines = self._workload_baselines(workload)

        # INDEX_VALIDATION=executed routes EVERY index type through the real-DDL path, which is the
        # only one that can MEASURE (hypopg builds a hypothetical index — there is nothing to execute
        # against, by construction it can only ever produce an estimate).
        if _index_validation_executed():
            return self._simulate_index_real_ddl(
                spec, query, ddl, original_cost, write_stats, write_overhead_pct, index_type,
                workload_baselines=workload_baselines,
            )

        optimized_cost = None
        index_size_bytes = None
        inner_error = None
        workload_costs: dict = {}
        engine = get_engine()
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT * FROM hypopg_create_index(:ddl)"), {"ddl": ddl})

                # SAVEPOINT isolates the EXPLAIN block — if it fails the transaction is not
                # fully aborted, so hypopg_reset() can still run to clean up.
                try:
                    conn.execute(text("SAVEPOINT hypopg_explain"))
                    result = conn.execute(text(f"EXPLAIN (FORMAT JSON) {query}"))
                    plan_with_index = result.fetchone()[0]
                    if isinstance(plan_with_index, list) and plan_with_index:
                        optimized_cost = float(plan_with_index[0]["Plan"]["Total Cost"])

                    # Workload loop — same session, virtual index still active. Each query
                    # gets its own savepoint so one failure doesn't poison the rest.
                    for b in workload_baselines:
                        try:
                            conn.execute(text("SAVEPOINT hypopg_workload"))
                            workload_costs[b["source"]] = self._explain_cost_on_conn(conn, b["sql"])
                            conn.execute(text("RELEASE SAVEPOINT hypopg_workload"))
                        except Exception:
                            workload_costs[b["source"]] = None
                            try:
                                conn.execute(text("ROLLBACK TO SAVEPOINT hypopg_workload"))
                            except Exception:
                                pass

                    # Nested savepoint for index_size: the column availability varies by
                    # hypopg version. If the query fails, the nested rollback restores
                    # the transaction state without aborting the outer block.
                    try:
                        conn.execute(text("SAVEPOINT hypopg_size"))
                        size_row = conn.execute(text(
                            "SELECT index_size FROM hypopg_list_indexes() LIMIT 1"
                        )).fetchone()
                        if size_row:
                            index_size_bytes = size_row[0]
                        conn.execute(text("RELEASE SAVEPOINT hypopg_size"))
                    except Exception:
                        index_size_bytes = None
                        try:
                            conn.execute(text("ROLLBACK TO SAVEPOINT hypopg_size"))
                        except Exception:
                            pass

                    conn.execute(text("RELEASE SAVEPOINT hypopg_explain"))
                except Exception as e:
                    inner_error = e
                    try:
                        conn.execute(text("ROLLBACK TO SAVEPOINT hypopg_explain"))
                    except Exception:
                        pass

                try:
                    conn.execute(text("SELECT hypopg_reset()"))
                except Exception:
                    pass
        except Exception as e:
            # hypopg doesn't support GIN/GiST — fall back to real CREATE → EXPLAIN → ROLLBACK.
            # PostgreSQL DDL is transactional: the index is created, EXPLAIN sees it, then
            # ROLLBACK removes it atomically. No DROP needed, no permanent schema change.
            if "not supported" in str(e).lower():
                return self._simulate_index_real_ddl(
                    spec, query, ddl, original_cost, write_stats, write_overhead_pct, index_type,
                    workload_baselines=workload_baselines,
                )
            return {
                "table": spec["target"],
                "column": spec["property"],
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
                "recommendation": f"simulation failed — {e}",
            }

        if inner_error:
            return {
                "table": spec["target"],
                "column": spec["property"],
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
                "recommendation": f"simulation failed — {inner_error}",
            }

        benefit_pct = None
        if original_cost is not None and optimized_cost is not None and original_cost > 0:
            benefit_pct = round(((original_cost - optimized_cost) / original_cost) * 100, 1)

        workload_impact = self._build_workload_impact(workload_baselines, workload_costs)

        if benefit_pct is None:
            rec = "simulation incomplete — cost comparison unavailable"
        elif benefit_pct > write_overhead_pct:
            rec = f"beneficial — read gain ({benefit_pct:.1f}%) outweighs write overhead ({write_overhead_pct:.1f}%)"
        else:
            rec = f"not recommended — read gain ({benefit_pct:.1f}%) does not justify write overhead ({write_overhead_pct:.1f}%)"
        rec = self._append_workload_note(rec, workload_impact)

        return {
            "table": spec["target"],
            "column": spec["property"],
            "index_type": index_type,
            "ddl": ddl,
            "estimated_benefit": spec.get("estimated_benefit", ""),
            "original_plan_cost": original_cost,
            "optimized_plan_cost": optimized_cost,
            "estimated_read_benefit_pct": benefit_pct,
            "write_overhead_pct": write_overhead_pct,
            "index_size_bytes": index_size_bytes,
            "table_writes_total": write_stats.get("writes_total"),
            "table_live_rows": live_rows,
            "recommendation": rec,
            "workload_impact": workload_impact,
        }

    @staticmethod
    def _timed_explain_analyze_on_conn(conn, sql: str, reps: int = 3) -> float | None:
        """Measured EXECUTION time of `sql` on an OPEN connection — warm-up discarded, median of N.

        WHY THIS EXISTS (2026-08-18, raised by the advisor: "make it clear HOW the index was built"):
        the index axis used to report the PLANNER'S COST ESTIMATE, while the rewrite axis reports time
        measured by executing at real scale. That is an inconsistency a reviewer reads in one sitting —
        and we have measured proof the estimate misleads: on 29c the cost model predicted a +3.46%
        regression where the real time regressed +29.4%, i.e. right direction, wrong magnitude by ~10x.

        Must run on the CALLER'S connection: the candidate index only exists inside that open
        transaction (CREATE INDEX ... then ROLLBACK), so opening a second connection would measure a
        database WITHOUT the index. This mirrors `_robust_explain_analyze` (executor.py) — same
        warm-up-then-median discipline the S=1 gate uses — but bound to an existing session.
        """
        import statistics
        # ⚠️ statement_timeout IS MANDATORY HERE (fixed 2026-08-24 — this was the orphan factory).
        #   This path ran FOUR unbounded EXPLAIN ANALYZE per index candidate (1 warm-up + 3 reps) with
        #   no statement_timeout at all, while every other execution path in this file sets one. The
        #   consequences, all traced back to here:
        #     * a run could exceed the client's 2400s HTTP budget; run_matrix then recorded a
        #       "timeout" and moved on — WITHOUT the server ever being told;
        #     * the abandoned statements kept executing, because nothing bounded them. Those are the
        #       ORPHAN queries: four found alive on 23/08, the oldest at 27.7 HOURS, and two more on
        #       24/08 still running after 15 minutes;
        #     * orphans compete for CPU with the live cell, and execution time IS the measurement —
        #       in the contaminated window 50% of runs timed out against 5% outside it.
        #   The timeline fits: this function was introduced on 18/08 with INDEX_VALIDATION=executed,
        #   and the daily count of ~40-minute client aborts climbed from ~5 to 23 right after.
        #   The budget is deliberately generous (the point is to MEASURE, not to cut short) but finite;
        #   RESET afterwards so the caller's transaction is not left with our setting.
        budget = int(os.getenv("INDEX_MEASURE_TIMEOUT_S", "180"))
        try:
            conn.execute(text(f"SET statement_timeout = '{budget}s'"))
            conn.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON) {sql}")).fetchone()   # warm-up, discarded
            times = []
            for _ in range(max(1, reps)):
                row = conn.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON) {sql}")).fetchone()
                plan = row[0]
                if isinstance(plan, list) and plan:
                    times.append(float(plan[0]["Execution Time"]))
            return statistics.median(times) if times else None
        except Exception:
            return None
        finally:
            try:
                conn.execute(text("RESET statement_timeout"))
            except Exception:
                pass

    def _simulate_index_real_ddl(
        self, spec: dict, query: str, ddl: str,
        original_cost: float | None, write_stats: dict,
        write_overhead_pct: float, index_type: str,
        workload_baselines: list[dict] | None = None,
    ) -> dict:
        """Build the candidate index FOR REAL and measure against it, then roll it back.

        BEGIN → CREATE INDEX → measure → ROLLBACK. PostgreSQL DDL is transactional, so the ROLLBACK
        removes the index atomically: no DROP, no permanent schema change, no window where a half-built
        index is visible to anyone else.

        TWO ROLES:
          * fallback for index types hypopg cannot simulate (GIN, GiST) — the original reason;
          * with INDEX_VALIDATION=executed, the DEFAULT path for every type, so the index axis is
            justified the same way the rewrite axis is: by EXECUTION, not by the planner's estimate.

        WHY THE SECOND ROLE EXISTS: reporting a cost estimate for indexes while reporting measured
        time for rewrites is an inconsistency a reviewer catches immediately — and the estimate is
        demonstrably unreliable in magnitude (29c: +3.46% predicted cost regression vs +29.4% real
        time regression). When executed, we record BOTH numbers, so the paper can show the gap
        instead of hiding it.
        """
        table = spec["target"]
        property_col = spec.get("property", "").strip("() ")

        sim_name = f"_athena_sim_{re.sub(r'[,\s]+', '_', table + '_' + property_col)}"[:63]
        sim_ddl = re.sub(r"CREATE INDEX\s+\S+\s+ON", f"CREATE INDEX {sim_name} ON", ddl, count=1)

        optimized_cost = None
        optimized_ms = None
        baseline_ms = None
        workload_costs: dict = {}
        workload_measured: dict = {}
        engine = get_engine()
        try:
            with engine.connect() as conn:
                # Baseline measured on the SAME session, BEFORE the index exists — same connection,
                # same cache state. Measuring it elsewhere would compare warm against cold.
                if _index_validation_executed():
                    baseline_ms = self._timed_explain_analyze_on_conn(conn, query)
                conn.execute(text("BEGIN"))
                try:
                    conn.execute(text(sim_ddl))
                    result = conn.execute(text(f"EXPLAIN (FORMAT JSON) {query}"))
                    plan = result.fetchone()[0]
                    if isinstance(plan, list) and plan:
                        optimized_cost = float(plan[0]["Plan"]["Total Cost"])
                    # MEASURED time with the index in place. Kept alongside the cost estimate on
                    # purpose: the pair (estimated, measured) is itself a result — it shows how far
                    # the planner's number is from reality on this workload.
                    if _index_validation_executed():
                        optimized_ms = self._timed_explain_analyze_on_conn(conn, query)
                    # Workload loop — same transaction, real index still present. The expensive
                    # CREATE is paid once; each extra EXPLAIN costs milliseconds.
                    for b in workload_baselines or []:
                        try:
                            workload_costs[b["source"]] = self._explain_cost_on_conn(conn, b["sql"])
                        except Exception:
                            workload_costs[b["source"]] = None

                    # WORKLOAD CONTRIBUTION, measured where it matters.
                    #
                    # The workload check answers "does this index HURT the other queries?" — the
                    # regression guard. Measuring all of them by execution is not affordable: 5
                    # recommendations x 4 workload queries x ~40s = ~17 min PER RUN.
                    #
                    # But 93% of those evaluations are NEUTRAL (the planner's cost does not move at
                    # all: 1878 of 2017 in the canonical cell) — the index simply does not touch that
                    # query, and there is nothing to measure. So the cheap estimate is used as a
                    # SCREEN and execution is spent only on the ones it flags as moving. That keeps
                    # the rigour exactly where the claim lives (~93 min for a whole cell instead of
                    # ~30 h) and is declared as such: neutral entries are estimated, moving ones are
                    # measured.
                    if _index_validation_executed():
                        for b in workload_baselines or []:
                            base_cost = b.get("baseline_cost")
                            new_cost = workload_costs.get(b["source"])
                            if not base_cost or new_cost is None:
                                continue
                            moved = abs(new_cost - base_cost) / base_cost > 0.005
                            if not moved:
                                continue
                            before = self._timed_explain_analyze_on_conn(conn, b["sql"], reps=1)
                            workload_measured[b["source"]] = before
                finally:
                    conn.execute(text("ROLLBACK"))
        except Exception as e:
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
                "table_live_rows": write_stats.get("live_rows", 0),
                "recommendation": f"simulation failed (real DDL) — {e}",
            }

        benefit_pct = None
        if original_cost is not None and optimized_cost is not None and original_cost > 0:
            benefit_pct = round(((original_cost - optimized_cost) / original_cost) * 100, 1)

        # MEASURED benefit — the number the paper should report when INDEX_VALIDATION=executed.
        # Kept separate from benefit_pct (the planner's estimate) so both travel in the record and
        # the gap between them can be reported rather than silently resolved.
        measured_benefit_pct = None
        if baseline_ms and optimized_ms and baseline_ms > 0:
            measured_benefit_pct = round(((baseline_ms - optimized_ms) / baseline_ms) * 100, 1)

        workload_impact = self._build_workload_impact(workload_baselines or [], workload_costs)

        # When we have a measured number, the RECOMMENDATION is made on it — not on the estimate.
        decide_pct = measured_benefit_pct if measured_benefit_pct is not None else benefit_pct
        how = "MEASURED by execution (warm-up + median of 3, index built for real then rolled back)" \
            if measured_benefit_pct is not None else "planner cost ESTIMATE (real DDL, rolled back)"
        if decide_pct is None:
            rec = "simulation incomplete — comparison unavailable"
        elif decide_pct > write_overhead_pct:
            rec = f"beneficial — read gain ({decide_pct:.1f}%) outweighs write overhead ({write_overhead_pct:.1f}%) [{how}]"
        else:
            rec = f"not recommended — read gain ({decide_pct:.1f}%) does not justify write overhead ({write_overhead_pct:.1f}%) [{how}]"
        rec = self._append_workload_note(rec, workload_impact)

        _measured = {
            # Both numbers travel together on purpose: `estimated_read_benefit_pct` is what the
            # planner predicts, `measured_read_benefit_pct` is what execution shows. Reporting only
            # the first is the inconsistency the advisor flagged; reporting only the second throws
            # away the comparison that makes the point.
            "baseline_exec_ms": baseline_ms,
            "optimized_exec_ms": optimized_ms,
            "measured_read_benefit_pct": measured_benefit_pct,
            "validation": "executed" if measured_benefit_pct is not None else "estimated",
            # Per-workload-query measured time, ONLY for entries the estimate flagged as
            # moving. Absent = the planner saw no change and none was measured.
            "workload_measured_ms": workload_measured or None,
        }

        return {
            "table": table,
            "column": property_col,
            "index_type": index_type,
            "ddl": ddl,
            "estimated_benefit": spec.get("estimated_benefit", ""),
            "original_plan_cost": original_cost,
            "optimized_plan_cost": optimized_cost,
            "estimated_read_benefit_pct": benefit_pct,
            "write_overhead_pct": write_overhead_pct,
            "index_size_bytes": None,
            "table_writes_total": write_stats.get("writes_total"),
            "table_live_rows": write_stats.get("live_rows", 0),
            "recommendation": rec,
            "workload_impact": workload_impact,
            **_measured,
        }

    def is_timeout_error(self, error_message: str) -> bool:
        return "QueryCanceled" in error_message or "statement timeout" in error_message.lower()

    def schema_fingerprint(self) -> str | None:
        try:
            engine = get_engine()
            with engine.connect() as conn:
                result = conn.execute(text(
                    "SELECT MD5(string_agg(indexname || indexdef, ',' ORDER BY indexname)) "
                    "FROM pg_indexes WHERE schemaname = 'public'"
                )).scalar()
            return result
        except Exception:
            return None

    def _run_explain(self, sql: str, analyze: bool, timeout_s: int | None = None, uri: str | None = None) -> dict:
        flags = "ANALYZE, FORMAT JSON" if analyze else "FORMAT JSON"
        try:
            engine = get_engine(uri)   # uri = instância SF1 (verify) p/ o fallback de perf quando o full-scale estoura
            with engine.connect() as conn:
                if analyze:
                    budget = timeout_s or int(os.getenv("EXPLAIN_ANALYZE_TIMEOUT_S", "120"))
                    conn.execute(text(f"SET statement_timeout = '{budget}s'"))
                result = conn.execute(text(f"EXPLAIN ({flags}) {sql}"))
                plan = result.fetchone()[0]
            execution_time_ms = plan[0].get("Execution Time") if analyze and isinstance(plan, list) else None
            return {"type": "postgres", "plan": plan, "execution_time_ms": execution_time_ms, "error": None}
        except Exception as e:
            return {"type": "postgres", "plan": {}, "execution_time_ms": None, "error": str(e)}
