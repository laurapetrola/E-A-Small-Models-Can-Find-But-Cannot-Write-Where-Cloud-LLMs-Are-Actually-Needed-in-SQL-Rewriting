import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

import sqlglot
import sqlglot.expressions as exp


# --- Corrector (WRITE-axis repair) — SQL-specific prompt + failure taxonomy ----------------------
# These are RELATIONAL-engine knowledge (CTEs/JOINs/aliases, Cartesian, UndefinedColumn). They live
# on the backend, NOT on the corrector node, so the node stays paradigm-agnostic: a future NoSQL
# backend supplies its own specialist corrector prompt + its own mechanical-failure markers.
def _flag(name: str, default: str = "off") -> bool:
    """Lê um lever opt-in do ambiente. Todo lever é OFF por default — um braço só ganha o lever quando o
    .env liga explicitamente, e o label do store carrega o sufixo (rastreabilidade: label + nome do doc +
    linha no INDICE). ⚠️ o servidor lê o .env no STARTUP: mudar exige reiniciar."""
    return os.getenv(name, default).strip().lower() in ("1", "true", "on", "yes")


_SQL_CORRECTOR_PROMPT = """A rewrite of the ORIGINAL query was REJECTED for a MECHANICAL error. The \
rewrite's OPTIMIZATION STRATEGY is correct — only the SQL mechanics are broken (e.g. an alias used \
but not declared in FROM/JOIN, a column-name mismatch, a base table dropped from FROM, a derived \
table referencing a sibling). FIX the mechanics so the SQL is VALID, while PRESERVING the exact same \
optimization structure (same CTEs, joins, window functions). Do NOT change the strategy, do NOT revert \
to the original, do NOT introduce a different optimization — repair ONLY the error.

RULES:
- Every column you reference MUST appear verbatim in the ORIGINAL query. The ORIGINAL uses the correct \
schema names; if the REJECTED rewrite renamed a column (e.g. dropped a `tbl_` prefix: `t.tbl_col` -> \
`t.col`), RESTORE the exact name from the ORIGINAL. Never invent a column name.
- Do NOT rename a column or alias that already matches the schema.
- Every joined table needs a join condition — never produce a Cartesian product.
- Keep every base table and every filter predicate the ORIGINAL had; do not drop any.
- Keep every numeric constant from the ORIGINAL — thresholds, multipliers, filter values (e.g. a \
`* 1.2` factor or `= 2000`). If the rewrite dropped or changed one, restore the exact value.

EXAMPLES (generic — t1/t2/x/y are placeholders):
1. Alias not declared: `SELECT a.x FROM t1 JOIN t2 ON t1.k = t2.k WHERE b.y > 0` (alias `b` never \
declared) -> use the declared alias: `... WHERE t2.y > 0`.
2. Column renamed/hallucinated: rewrite references `t1.col` but the ORIGINAL and schema use \
`t1.t1_col` -> restore `t1.t1_col`.
3. Base table dropped: the ORIGINAL filtered on `t2.k = t1.k` but the rewrite removed `t2` from FROM \
-> reintroduce `t2` and its join condition; do not drop the predicate.
4. Clause out of order (DO NOT change the strategy — only the placement): a JOIN/CROSS JOIN/LATERAL \
placed AFTER the WHERE is invalid -> move EVERY join before the WHERE: `FROM a WHERE p CROSS JOIN b` \
becomes `FROM a CROSS JOIN b WHERE p`. A precomputed-aggregate derived table goes in the FROM list \
(`FROM (SELECT ...) d, a WHERE p`). A query must have exactly ONE WHERE — merge duplicates with AND.
5. Column out of scope — a CTE/derived table did NOT project it: the rewrite wraps `t1` in \
`c AS (SELECT t1.a FROM t1 ...)` but the outer query references `c.b` ("column ... does not exist") \
-> `b` was never projected by `c`. ADD `b` to the CTE's SELECT list, OR reference it where `t1` is \
still in scope. NEVER reference through a CTE/derived table a column it does not expose.
6. Table referenced but absent from FROM ("missing FROM-clause entry for table x"): the rewrite uses \
`x.col` but `x` is not in the FROM/JOIN of THAT query level (often a sibling CTE/subquery can't see \
another's tables) -> ADD `x` to that level's FROM with its join condition, OR qualify the column with \
a table that IS in scope at that level. A CTE cannot reference a table that lives only inside another CTE.

{schema}ORIGINAL:
{original}

REJECTED REWRITE:
{broken}

ERROR:
{error}
{history}
{tail}"""

# Degrau 0-2: devolve SÓ o SQL (sem raciocínio). Degrau 3 (CoT): raciocina ANTES de escrever — mesmo
# coder, só o prompt muda (Wei et al. 2022). extract_query pega o ÚLTIMO bloco ```sql, ignorando a prosa.
# Degrau 4 (GERAR-da-estratégia = mecanismo QUITE encolhido): em vez de PATCHAR o botch, o FIND-agent
# descreve a estratégia EM PALAVRAS e o coder ESCREVE do zero — não herda o SQL quebrado. Dois prompts:
_SQL_STRATEGY_PROMPT = """You are given an ORIGINAL SQL query and a REWRITE ATTEMPT that tried to optimize \
it (the attempt may be broken — ignore its mistakes). In 2-4 sentences, describe the OPTIMIZATION STRATEGY \
the attempt is going for — ABSTRACTLY, in plain words (e.g. "decorrelate the scalar subquery into a join", \
"pre-aggregate via a CTE then join", "reorder the joins so the selective filter runs first", "convert the \
IN-subquery to a semi-join", "push the predicate below the aggregation"). Do NOT write any SQL. Do NOT \
mention specific literal values. Output ONLY the strategy description.

ORIGINAL:
{original}

REWRITE ATTEMPT:
{broken}

OPTIMIZATION STRATEGY (plain words, no SQL):"""

_SQL_GENERATE_PROMPT = """Rewrite the ORIGINAL SQL query to apply the given OPTIMIZATION STRATEGY. Write the \
rewrite FROM SCRATCH from the strategy — there is NO broken attempt to copy. The rewrite MUST return EXACTLY \
the same result-set as the ORIGINAL (same rows, same multiplicity, same columns in the same order).

{dialect}RULES:
- Apply the STRATEGY — do not invent a different optimization, do not just return the ORIGINAL unchanged.
- Every column you reference MUST appear verbatim in the ORIGINAL query; never invent or rename a column.
- Every joined table needs a join condition — never produce a Cartesian product.
- Keep every base table, every filter predicate, and every numeric constant the ORIGINAL had.
{semguard}{schema}
ORIGINAL:
{original}

OPTIMIZATION STRATEGY:
{strategy}
{error}{history}
{tail}"""

# Juiz de ADERÊNCIA (LLM-as-judge): o writer aplicou a técnica da ESTRATÉGIA, ou re-otimizou sozinho?
_SQL_ADHERENCE_PROMPT = """You are auditing whether a SQL REWRITE implemented a given OPTIMIZATION STRATEGY \
that ANOTHER agent decided. You are NOT judging if the rewrite is fast or correct — ONLY whether it applies \
the SAME optimization TECHNIQUE the strategy describes, or a DIFFERENT one.

Rules:
- Answer on the FIRST line with ONE word: ADHERENT (the rewrite applies the strategy's technique) or \
DEVIATED (the rewrite uses a clearly DIFFERENT optimization than the strategy).
- Second line: a short reason (which technique the strategy asked for vs what the rewrite did).
- IGNORE any index / DDL / "create index" items in the strategy — the rewrite cannot create indexes; judge \
ONLY the SQL-rewrite technique (decorrelation, join reorder, CTE/materialization, IN→semi-join, etc.).
- If the rewrite applies the strategy's MAIN rewrite technique (even if it also does minor extra tidying), \
that is ADHERENT.

STRATEGY (decided by the architect):
{strategy}

ORIGINAL:
{original}

REWRITE (written by the writer):
{rewrite}"""

# Modo 2-AGENTES (TWO_AGENT_MODE): o architect DECIDE a estratégia (sem SQL); o writer escreve dela.
_SQL_STRATEGY_DECISION_PROMPT = """You are a SQL optimization ADVISOR. Read {read_src} and DECIDE the \
optimization strategy — DO NOT write SQL.

{db}{hw}{plan}
Your task: {find_line} and DECIDE the strategy as a STRUCTURED LIST.

For EACH opportunity, ONE line:
  N. <TECHNIQUE> — TARGET: <the EXACT target in THIS query: the specific table / column / subquery / join> \
— TRANSFORMATION: <what to do, concretely>

Rules:
- SPECIFICITY is mandatory: name the REAL target from {from_src} — the specific table, column, subquery, or \
join in THIS query — NEVER a vague "a large table".
- YOU decide the technique from {from_src} — there is NO predefined list.
- Order the ops from the one that removes the most rows/cost to the least.
- DO NOT write SQL. DO NOT invent columns absent from the query/schema.
- The rewrite (written later by another agent) MUST return the IDENTICAL result-set — propose only \
equivalence-preserving transformations.
{rwfocus}{learned}{schema}ORIGINAL QUERY:
{original}
{prev}
OPTIMIZATION STRATEGY (structured list, one op per line, NO SQL):"""

_CORRECTOR_TAIL_DIRECT = "Return ONLY the corrected SQL — no markdown, no explanation, no code fences."
_CORRECTOR_TAIL_COT = (
    "First REASON step by step (2-4 sentences): name EXACTLY what is broken (which alias/column/table/"
    "clause) and the MINIMAL fix that keeps the SAME optimization strategy. THEN output the corrected "
    "SQL inside a single ```sql ... ``` block. Do NOT change the strategy; do NOT revert to the original."
)

# Markers of a class-(b) WRITE failure (model reached the idea, botched the SQL). NOT a timeout
# (strategic — kept the slow query, nothing mechanical to repair) nor S!=1 alone (semantic).
_SQL_MECHANICAL_MARKERS = (
    "structural_error", "data_loss", "syntax error", "cartesian",
    "undefinedcolumn", "undefined column", "undefinedtable", "undefined table",
    "unknown sources",
    # MySQL phrasings of the same class-(b) column/table reference errors (PG says "undefined …").
    "unknown column", "unknown table",
    # Rich S≠1 equivalence feedback (degrau 2): a PRIVACY-SAFE structural diff (counts/shape only,
    # never a data value) — see equivalence_diff. The marker lets sanitize forward it to the coder.
    "result-set mismatch",
)


@dataclass
class Transform:
    """A registered DETERMINISTIC rewrite (the CLOSED set production can apply; the dispatcher iterates
    these and the NODE never names one). Production has NO LLM: every transform is a detector — the system
    recognizes the shape and writes the SQL; the LLM's role is DISCOVERY (free-form, offline). Fields:
      - recognize: STRUCTURAL match on parsed components -> params|None. Agnostic: matches a shape (AST
        structure), never a benchmark table/column literal, so it generalizes across schemas.
      - build: (backend, components) -> SQL|None. The SHARED reconstruction; dialect differences are
        resolved through the backend (self), not branched here.
      - label: optional technique label surfaced as a suggestion when the transform fires."""
    name: str
    recognize: Callable[[dict], dict | None]
    build: Callable[..., str | None]
    label: str | None = None


class SQLBackend(ABC):
    """Common relational SQL backend — subclass and implement the abstract methods for each RDBMS."""

    # ── Abstract — must be implemented per RDBMS ──────────────────────────────

    @abstractmethod
    def sqlglot_dialect(self) -> str: ...

    @abstractmethod
    def syntax_hint(self) -> str: ...

    @abstractmethod
    def explain(self, query: str, uri: str | None = None) -> dict: ...

    @abstractmethod
    def explain_analyze(self, query: str, timeout_s: int | None = None, uri: str | None = None) -> dict: ...

    @abstractmethod
    def hardware_hints(self) -> dict: ...

    @abstractmethod
    def get_total_cost(self, explain_result: dict) -> float | None: ...

    @abstractmethod
    def extract_table_estimates(self, explain_result: dict) -> dict[str, int]: ...

    @abstractmethod
    def sample_hash(self, query: str, timeout_s: int | None = None, uri: str | None = None) -> tuple[str | None, str | None]: ...

    @abstractmethod
    def get_partial_index_predicates(self, table_name: str) -> dict[str, str]: ...

    @abstractmethod
    def extract_schema(self) -> tuple[list[dict], list[dict]]: ...

    @abstractmethod
    def ping_latency(self) -> float: ...

    @abstractmethod
    def db_version(self) -> str: ...

    # ── Concrete — shared relational SQL logic ─────────────────────────────────

    def _info_schema_filter(self) -> str:
        """WHERE fragment to restrict information_schema.columns to the current schema.
        PostgreSQL default: public. MySQL backend overrides with DATABASE()."""
        return "table_schema = 'public'"

    def normalize_implicit_joins(self, sql: str) -> str:
        """Convert implicit comma-join syntax to explicit INNER JOINs.
        Resolves unqualified column names via information_schema so the LLM never
        sees ON TRUE. Returns original sql unchanged on any parse or lookup failure."""
        from src.connections import get_engine
        from sqlalchemy import text as _text

        try:
            ast = sqlglot.parse_one(sql, dialect=self.sqlglot_dialect())
        except Exception:
            return sql

        if not isinstance(ast, exp.Select):
            return sql

        joins = ast.args.get("joins") or []
        implicit = [j for j in joins if j.args.get("on") is None and j.args.get("using") is None]
        if not implicit:
            return sql  # already explicit or no joins at all

        from_clause = ast.args.get("from_")
        if not from_clause or not isinstance(from_clause.this, exp.Table):
            return sql

        def _tinfo(node: exp.Table):
            return (node.alias or node.name).lower(), node.name.lower()

        tables = [_tinfo(from_clause.this)] + [
            _tinfo(j.this) for j in joins if isinstance(j.this, exp.Table)
        ]
        table_names = list({name for _, name in tables})

        # Resolve column → table via information_schema
        col_to_table: dict[str, str | None] = {}
        try:
            placeholders = ",".join(f"'{t}'" for t in table_names)
            engine = get_engine()
            with engine.connect() as conn:
                rows = conn.execute(_text(
                    f"SELECT column_name, table_name FROM information_schema.columns "
                    f"WHERE table_name IN ({placeholders}) AND {self._info_schema_filter()}"
                )).fetchall()
            for col, tbl in rows:
                key = col.lower()
                val = tbl.lower()
                if key not in col_to_table:
                    col_to_table[key] = val
                elif col_to_table[key] != val:
                    col_to_table[key] = None  # ambiguous — ignore
        except Exception:
            return sql

        if not col_to_table:
            return sql

        # Classify WHERE conditions
        where = ast.args.get("where")
        if not where:
            return sql

        def _flatten_and(node):
            if isinstance(node, exp.And):
                return _flatten_and(node.left) + _flatten_and(node.right)
            return [node]

        join_graph: dict[tuple, list] = {}
        scalar_filters = []

        def _qualify(col_node: exp.Column, col_to_table: dict) -> exp.Column:
            """Return a copy of col_node with table qualifier set from col_to_table."""
            tbl = col_to_table.get(col_node.name.lower())
            if not tbl:
                return col_node
            qualified = col_node.copy()
            qualified.set("table", exp.Identifier(this=tbl))
            return qualified

        for cond in _flatten_and(where.this):
            if isinstance(cond, exp.EQ):
                l, r = cond.left, cond.right
                if isinstance(l, exp.Column) and isinstance(r, exp.Column) and not l.table and not r.table:
                    lt = col_to_table.get(l.name.lower())
                    rt = col_to_table.get(r.name.lower())
                    if lt and rt and lt != rt:
                        # Qualify both sides so the LLM can reassign ON conditions when reordering joins
                        qualified_cond = exp.EQ(this=_qualify(l, col_to_table), expression=_qualify(r, col_to_table))
                        join_graph.setdefault(tuple(sorted([lt, rt])), []).append(qualified_cond)
                        continue
            scalar_filters.append(cond)

        if not join_graph:
            return sql  # couldn't identify any join predicates

        # Rebuild joins with ON conditions
        alias_map = {alias: name for alias, name in tables}
        joined = {tables[0][1]}
        new_joins = []

        for alias, name in tables[1:]:
            on_conds = []
            for jt in list(joined):
                key = tuple(sorted([name, jt]))
                if key in join_graph:
                    on_conds.extend(join_graph.pop(key))

            orig = next((j for j in joins if (j.this.alias or j.this.name).lower() == alias), None)
            if orig is None:
                return sql

            new_join = orig.copy()
            if on_conds:
                on_expr = on_conds[0]
                for extra in on_conds[1:]:
                    on_expr = exp.And(this=on_expr, expression=extra)
                new_join.set("on", on_expr)
            new_joins.append(new_join)
            joined.add(name)

        # Qualify unqualified column references in scalar filters so parse_components
        # can classify them by table alias (needed for reconstruct_query to emit WHERE)
        def _qualify_node(node):
            if isinstance(node, exp.Column) and not node.table:
                tbl = col_to_table.get(node.name.lower())
                if tbl:
                    qualified = node.copy()
                    qualified.set("table", exp.Identifier(this=tbl))
                    return qualified
            return node

        qualified_filters = []
        for f in scalar_filters:
            qualified_filters.append(f.transform(_qualify_node))

        # Remaining predicates that couldn't be placed stay in WHERE
        leftover = [p for preds in join_graph.values() for p in preds] + qualified_filters
        if leftover:
            new_where = leftover[0]
            for c in leftover[1:]:
                new_where = exp.And(this=new_where, expression=c)
            ast.set("where", exp.Where(this=new_where))
        else:
            ast.set("where", None)

        ast.set("joins", new_joins)

        try:
            return ast.sql(dialect=self.sqlglot_dialect())
        except Exception:
            return sql

    def validate_syntax(self, query: str) -> tuple[bool, str]:
        try:
            sqlglot.parse_one(query, dialect=self.sqlglot_dialect())
            return True, ""
        except sqlglot.errors.SqlglotError as e:
            return False, str(e)

    def extract_references(self, query: str) -> set[str]:
        try:
            ast = sqlglot.parse_one(query, dialect=self.sqlglot_dialect())
            cte_names = {cte.alias.lower() for cte in ast.find_all(exp.CTE)}
            return {t.name.lower() for t in ast.find_all(exp.Table) if t.name.lower() not in cte_names}
        except Exception:
            return set()

    def parse_query(self, query: str) -> dict | None:
        from src.pipeline.backends.relational.sql_rewriter import (
            parse_components, parse_decorrelation_components, parse_consolidation_components,
        )
        comps = parse_components(self.normalize_implicit_joins(query), self.sqlglot_dialect())
        if comps is None:
            # Shapes that bail parse_components but a SEEDED transform handles: their own recognizers
            # expose the components so the decomposed path REACHES the deterministic transform (arch A)
            # instead of falling straight to free-form. Tried in order; each returns None if absent.
            for recognizer in (parse_decorrelation_components, parse_consolidation_components):
                try:
                    comps = recognizer(query, self.sqlglot_dialect())
                except Exception:
                    comps = None
                if comps is not None:
                    break
        return comps

    def decorrelate(self, components: dict) -> str | None:
        """Apply the seeded DETERMINISTIC decorrelation transform (architecture A). The CODE writes the
        SQL — the model is out of FIND and WRITE for this shape — so it rewrites even a model that would
        never suggest the technique. Returns the rewritten SQL or None if the shape is absent."""
        from src.pipeline.backends.relational.sql_rewriter import _reconstruct_decorrelation
        return _reconstruct_decorrelation(components)

    def consolidate(self, components: dict) -> str | None:
        """Apply the seeded DETERMINISTIC consolidation transform (architecture A): N repeated
        scalar-aggregate subqueries over one table -> a single scan with conditional aggregates. The CODE
        writes the SQL (model out of FIND and WRITE), so even a model that never suggests it gets the
        rewrite. Returns the rewritten SQL or None if the shape is absent."""
        from src.pipeline.backends.relational.sql_rewriter import _build_consolidation
        return _build_consolidation(self, components)

    def bucket_agg_expr(self, agg_node, pred_node) -> str:
        """Per-bucket conditional aggregate for the consolidation transform. Standard SQL / PostgreSQL:
        `agg(...) FILTER (WHERE pred)`. Engines without FILTER (MySQL) OVERRIDE this with the
        `AGG(CASE WHEN pred THEN arg END)` form. The dialect difference lives HERE (backend), never in the
        shared builder — so the builder and the node stay engine-agnostic."""
        d = self.sqlglot_dialect()
        return f"{agg_node.sql(dialect=d)} FILTER (WHERE {pred_node.sql(dialect=d)})"

    def transforms(self) -> list[Transform]:
        """Registry of DETERMINISTIC rewrite transforms — the CLOSED set production can apply, with NO
        LLM. Subclasses INHERIT this list; engines override the builders' dialect hooks, not the registry
        itself. A new technique is APPENDED here only after free-form discovery has formalized it (one
        recognizer + one builder). recognize is purely structural (agnostic), so each entry generalizes
        across schemas. Each entry is a detector: the system recognizes the shape and writes the SQL."""
        return [
            Transform(
                "decorrelation",
                recognize=lambda c: c if c.get("decorrelation") is not None else None,
                build=lambda be, c: be.decorrelate(c),
                label="decorrelate scalar aggregate subquery into CTE plus join",
            ),
            Transform(
                "consolidation",
                recognize=lambda c: c if c.get("consolidation") is not None else None,
                build=lambda be, c: be.consolidate(c),
                label="consolidate repeated scalar-aggregate subqueries into a single scan",
            ),
        ]

    def optimize_decomposed(self, raw_sql: str, components: dict) -> tuple[str | None, dict]:
        """DETECTOR-ONLY decomposed optimization — PRODUCTION (DISCOVERY_MODE=off), NO LLM. The architect
        calls this only OUTSIDE discovery, so detectors fire whenever a shape matches (no separate mode
        flag). Iterates the transform registry (closed set of deterministic detectors): structural
        recognize -> the SHARED builder writes the SQL (the runtime S=1 gate is the equivalence backstop).
        The LLM never writes or chooses here; its role is DISCOVERY (free-form, offline). The node never
        learns which transform fired — it reads only the returned provenance.

        Returns (sql|None, provenance{transform, trigger, suggestions}):
          - (sql, {transform: T, trigger: 'detector'})  a mapped transform applied;
          - (None, {trigger: 'none'})                    no mapped transform -> production leaves the
                                                          query unchanged (free-form happens only in
                                                          DISCOVERY, handled by the node)."""
        import logging
        log = logging.getLogger(self.__class__.__name__)
        for t in self.transforms():
            if t.recognize(components) is None:
                continue
            sql = t.build(self, components)
            if sql and sql.strip() != raw_sql.strip():
                log.info(f"[decomposed] detector '{t.name}' fired")
                return sql, {"transform": t.name, "trigger": "detector", "suggestions": [t.label] if t.label else []}
        return None, {"transform": None, "trigger": "none", "suggestions": []}

    def build_fallback_prompt(self, raw_query: str, explain: dict, errors: list[str], hw_hints: dict, retry_hint: str | None, prior_attempts: list[str] | None = None, schema_context: str = "") -> str:
        plan_section = (
            f"Physical Execution Plan:\n{explain['plan']}"
            if not explain.get("error")
            # ⚠️ 29/08: plano retido → bloco VAZIO. Antes injetava "Execution plan unavailable:
            # withheld for ablation (PLAN_HINTS=off) — optimize from the query STRUCTURE + schema only",
            # que prometia schema ausente e vazava vocabulário do experimento. Ver a nota em
            # build_strategy_decision_prompt.
            else ""
        )
        error_section = (
            "\nPrevious rewrite was rejected for the following reasons — fix all of them:\n"
            + "\n".join(f"- {e}" for e in errors)
            if errors
            else ""
        )
        # "Experience" — prior attempts this session, so the model does not loop on a dead end (inspired by
        # LLM4IA's experience memory). NUANCED for rewrite (unlike index, where no-benefit = just drop): a
        # prior attempt may have failed on a SQL MECHANICS error while the STRATEGY was correct — that should
        # be RETRIED with the bug fixed, NOT blacklisted. Only a VALID-but-no-faster approach is a true dead end.
        experience_section = ""
        _prior = [a for a in (prior_attempts or []) if a and a.strip()][-3:]  # bound context to last 3
        if _prior:
            experience_section = (
                "\nApproaches you ALREADY tried this session (do not loop):\n"
                + "\n".join(f"  [attempt {i+1}]\n{a.strip()}" for i, a in enumerate(_prior))
                + "\n- If a prior attempt failed on a SQL MECHANICS error (invalid syntax, dropped table, "
                "alias/scope), the STRATEGY may be fine — you MAY retry it, but FIX the error.\n"
                "- If a prior attempt was VALID but NOT faster (no gain / regression), that approach is a dead "
                "end — try a STRUCTURALLY DIFFERENT strategy.\n"
                "- Never resubmit an identical query.\n"
            )
        retry_section = (
            f"\nQuality retry — previous approved result was weak: {retry_hint}\n"
            "Try a meaningfully different rewrite strategy.\n"
            if retry_hint else ""
        )
        hw_section = ""
        if hw_hints:
            lines = ["Hardware context:"]
            if hw_hints.get("cache_hit_ratio_pct") is not None:
                ratio = hw_hints["cache_hit_ratio_pct"]
                io_note = "most data in memory, random I/O cheap" if ratio >= 90 else "significant disk I/O expected, random reads expensive"
                lines.append(f"  Buffer cache hit ratio: {ratio}% — {io_note}")
            if hw_hints.get("work_mem_mb") is not None:
                lines.append(f"  Work memory per operation: {hw_hints['work_mem_mb']:.1f} MB — memory available per sort or hash join")
            if hw_hints.get("random_page_cost") is not None and hw_hints.get("seq_page_cost") is not None:
                ratio = hw_hints["random_page_cost"] / hw_hints["seq_page_cost"]
                lines.append(f"  Random/Sequential I/O cost ratio: {ratio:.1f}x — {'low, index scans are relatively cheap' if ratio <= 2 else 'high, sequential scans preferred over many random reads'}")
            hw_section = "\n".join(lines) + "\n"

        db_ver = self.db_version()
        db_section = f"Database: {db_ver}\n" if db_ver else ""
        # B-write (opt-in SCHEMA_LINKING no raw): colunas reais das tabelas → o raw architect não alucina
        # coluna/alias ao ESCREVER (a mesma classe de mechanics de escopo que o coder cometia no aided).
        schema_section = (f"\nRelevant table columns (real schema — use the EXACT names):\n{schema_context}\n"
                          if schema_context else "")

        # Architecture B (rule-in-prompt): in APPLICATION mode, inject the validated discovered rules
        # so the model APPLIES known techniques (the corrector then repairs the SQL). EMPTY in discovery
        # mode — the prompt stays neutral/unseeded there (see learned_rules.py).
        from src.pipeline.discovery.learned_rules import learned_rules_text
        _learned = learned_rules_text()
        learned_section = (
            "\nDiscovered optimization techniques — apply when the query's shape matches (the result set "
            "MUST stay identical; if none applies, ignore them):\n" + _learned + "\n"
            if _learned else ""
        )

        # PLAN_HINTS ablation: when the plan is WITHHELD (explain carries an error), the plan-reading
        # Step 1/Step 2 would be self-contradictory ("read the plan below" + "plan unavailable"). Swap them
        # for a STRUCTURE-only instruction — same non-prescriptive discipline (no fix named; "WHICH transform
        # is your decision" preserved). ON-PATH (plan present) is BYTE-IDENTICAL to before (else branch).
        plan_withheld = bool(explain.get("error"))
        # ⚠️ CONFUNDIMENTO DA ABLAÇÃO DO PLANO (achado 21/08) — e como PROMPT_HINT_PARITY o resolve.
        #   Os dois ramos diferiam em DUAS coisas, não uma:
        #     (a) a EVIDÊNCIA — plano+hardware presentes ou ausentes  ← o que a ablação quer medir
        #     (b) o TEXTO DA PISTA — o ramo OFF nomeia "subqueries evaluated repeatedly", que é a
        #         direção de `Materialize Subquery`; o ramo ON nomeia "full scans, nested loops,
        #         mis-estimation", que é a direção de acesso/índice.
        #   A q69 rendeu 51,2% no OFF contra 11,2% no ON, e o vocabulário decidido pelo FIND seguiu
        #   exatamente a pista de cada ramo. Ou seja: parte da diferença pode ser a INSTRUÇÃO, não o
        #   plano — e essa claim hoje sustenta o cancelamento de US$ 19,38 em células.
        #
        #   PROMPT_HINT_PARITY=on dá ao ramo COM plano a MESMA pista do ramo sem plano, isolando (a).
        #   ⛔ NÃO ligar em célula de produção sem declarar: muda o prompt e quebra comparabilidade
        #      com tudo que já rodou. É lever de SONDA.
        _hint_parity = os.getenv("PROMPT_HINT_PARITY", "off").strip().lower() in ("1", "true", "on", "yes")
        _shape_hint = ("subqueries evaluated repeatedly, large joins, repeated scans of the same table")

        if plan_withheld:
            evidence_section = (
                "Look at the query STRUCTURE below and decide the optimization — NO execution plan is available; "
                "reason from the SQL text alone. Identify what likely drives the cost from the query shape "
                f"({_shape_hint}).\n\n"
                "WHICH transformation to apply — if any — is entirely your decision; the prompt does not prescribe one."
            )
            noop_line = "If no actionable improvement is apparent from the query, return the query unchanged."
        else:
            _parity_line = (
                f"\nAlso consider what the query SHAPE suggests ({_shape_hint}).\n"
                if _hint_parity else ""
            )
            evidence_section = (
                "Step 1 — Read the physical execution plan below and identify the operations driving the cost:\n"
                "- Full scans on large tables\n"
                "- Nested loops with a large outer row count (the inner side runs once per outer row → many round-trips)\n"
                "- Operations where the planner's row estimate diverges sharply from the actual count (mis-estimation)\n"
                "- Which operations dominate the total estimated/actual cost\n"
                "\n"
                "Step 2 — Interpret those operations against the hardware context above:\n"
                "- Nested Loop with many iterations + low cache hit ratio → random I/O is expensive\n"
                "- Hash Join → whether the smaller relation fits within work_mem\n"
                "- Seq Scan on a large table + low cache hit ratio → sequential reads dominate the cost\n"
                "\n"
                + _parity_line +
                "Then rewrite based on this evidence. WHICH transformation to apply — if any — is entirely your decision; the prompt does not prescribe one. Use whatever the plan and hardware support."
            )
            noop_line = "If the plan and hardware context show no actionable improvement, return the query unchanged."

        # AGNOSTIC, NON-PRESCRIPTIVE prompt. It gives the GOAL + the EVIDENCE (execution plan +
        # hardware context) + agnostic guidance on how to READ that evidence (which plan operations
        # are expensive, how to interpret them against the hardware) + the semantic-equivalence
        # constraints. It deliberately does NOT prescribe WHICH transform to apply (no "decorrelate",
        # "reorder joins", "push filters") and contains NO benchmark-specific pattern — that would
        # overfit the result to these queries and turn the model into a follower, not an optimizer.
        # The transform is the model's to choose from its own knowledge; the equivalence gate (S=1)
        # is the safety net. Reading the plan + hardware is agnostic (holds for any relational DB).
        # PLAN-READING is the PREMISE of the system (a "plan-aware advisor") and is ESSENTIAL — the model is
        # NEVER left in the dark: it gets the plan + hardware + HOW TO READ them (which operations are
        # expensive: full scans, nested loops/large-outer-count, mis-estimation) and how to interpret them
        # against the hardware (Step 2). That is plan LITERACY, agnostic, true for any relational DB.
        # What is REMOVED (the FIND must stay the model's) is the FIX-DIRECTION: the earlier prompt listed
        # "join orderings that produce large intermediate results early" (= reorder) and "filters applied
        # after expensive joins" (= pushdown) — those PRESCRIBE a transform and contaminate the capability
        # measurement. Line below ("WHICH transformation … is your decision") makes the boundary explicit.
        # So: plan+hardware literacy = given (essential); WHICH technique to apply = the model decides (FIND).
        return f"""{retry_section}You are a query optimizer. Rewrite the query below to make it faster (reduce execution cost). The rewrite MUST return exactly the same rows as the original.

{db_section}{self.syntax_hint()}
{hw_section}
{evidence_section}
{learned_section}
Semantic preservation rules (non-negotiable — the result set must be identical):
- Every table present in the original FROM clause must appear in the rewritten query
- Implicit comma-joins are INNER JOINs — use INNER JOIN, never LEFT JOIN or OUTER JOIN unless the original explicitly used them
- All WHERE conditions from the original must be preserved — do not remove any filter
- IN (subquery) and EXISTS patterns: do NOT convert these to CTEs or JOIN chains (their semi-join/anti-join and NULL semantics are easy to break) — leave the IN/EXISTS clause itself exactly as written, but you MAY still optimize the rest of the query around it
- WITH clauses (CTEs): preserve ALL existing CTE definitions exactly as written
- If you introduce a derived table or CTE, give every output column an explicit alias (e.g. `col AS renamed`, `expr AS computed_value`) — never rely on the source column name being carried through; reference exactly the alias you define

SQL validity (write SQL that PARSES and RUNS — generic syntax, no bearing on WHICH technique to use):
- Every subquery starts with SELECT: write `(SELECT agg(col) FROM t WHERE p)`, never `(agg(col) FROM t ...)`
- Close every construct: each CASE ends with END; every opening parenthesis has a matching close
- Put every JOIN / CROSS JOIN / LATERAL BEFORE the WHERE clause; use exactly ONE WHERE per query level (combine conditions with AND)
- Reference only tables/CTEs that exist in the original or that you define in THIS query — never invent a table or CTE name

{noop_line}
{schema_section}
Query:
{self.normalize_implicit_joins(raw_query)}

{plan_section}
{error_section}{experience_section}

Return ONLY valid SQL — no explanation, no markdown, no code blocks, no trailing semicolon."""

    def corrector_should_fire(self, errors: list[str], raw_sql: str, optimized_sql: str) -> bool:
        """AGNOSTIC gate the routing calls — keeps the SQL failure/targeting taxonomy OFF the graph.
        Fire the (budgeted) corrector only on a MECHANICAL failure AND on an attempt that REACHED a
        technique worth repairing — not one that merely kept the original shape (e.g. still-correlated),
        which has nothing transformed to mechanically repair. A NoSQL backend defines its own notions."""
        return self.is_mechanical_failure(errors) and not self.attempt_kept_correlation(raw_sql, optimized_sql)

    def is_mechanical_failure(self, errors: list[str]) -> bool:
        """True for class-(b) mechanical SQL failures the corrector can repair; False for timeout/no-op.
        Engine-specific failure taxonomy (a NoSQL backend overrides with its own markers)."""
        blob = " ".join(errors or []).lower()
        if "timeout" in blob:
            return False
        return any(m in blob for m in _SQL_MECHANICAL_MARKERS)

    def attempt_kept_correlation(self, raw_sql: str, optimized_sql: str) -> bool:
        """Corrector TARGETING signal. True when the ORIGINAL is the decorrelation shape (a correlated
        scalar-aggregate subquery) AND the optimized attempt STILL carries that correlation — i.e. the
        model did NOT reach the decorrelation, so a mechanical repair here would only patch a non-
        transform (≈ the original). The corrector budget is precious: SAVE it for the attempt that
        REACHED the technique (correlation removed) but slipped on the mechanics — that is the legitimate
        class-(b)→(a) repair. The model's FIND capability stays measured by whether it reached the
        technique; this only changes WHICH failed attempt the mechanical coder is spent on, never WHAT
        it may do (still mechanical-only, §5.3). Returns False for non-decorrelation queries (signal N/A
        → corrector fires as before) and False on any parse failure (never block the corrector)."""
        try:
            from src.pipeline.backends.relational.sql_rewriter import detect_correlated_scalar_aggregate
            d = self.sqlglot_dialect()
            if detect_correlated_scalar_aggregate(raw_sql, d) is None:
                return False  # original isn't the decorrelation shape — targeting doesn't apply
            return detect_correlated_scalar_aggregate(optimized_sql, d) is not None
        except Exception:
            return False

    def top_node_actual_rows(self, explain_result: dict) -> int | None:
        """Top-node ACTUAL output rows from an EXPLAIN ANALYZE plan = the query's TRUE result
        cardinality (respecting its own LIMIT). A cheap cross-check that augments the LIMIT-100 sample
        hash: on a query returning >100 rows the sample only sees the head, so two queries that agree
        on the top 100 but differ in total count slip through — this catches that. MUST be ACTUAL rows
        (from ANALYZE), never the planner's ESTIMATE (which our own cardinality findings show is wrong
        by orders of magnitude). Default None ⇒ gate is a no-op (engine's ANALYZE plan not parsed yet);
        engines override. None on either side ⇒ the sample hash stands alone (e.g. original timed out)."""
        return None

    def _ordered_sample_sql(self, conn, stripped: str, n: int) -> str:
        """Deterministic sampling SQL for sample_hash/sample_rows: ORDER BY **ALL** columns (positional),
        not just column 1 — else, on a result >n rows where column 1 TIES across the n-th-row boundary,
        WHICH rows the LIMIT keeps is plan-dependent → two equivalent queries can hash differently
        (false S≠1). Probes the column count with a LIMIT 0 (planned, not executed → cheap). Positional
        ORDER BY also sidesteps duplicate output-column names."""
        from sqlalchemy import text
        ncols = len(conn.execute(text(f"SELECT * FROM ({stripped}) AS _s LIMIT 0")).keys())
        order = ", ".join(str(i) for i in range(1, ncols + 1)) or "1"
        return f"SELECT * FROM ({stripped}) AS _sample ORDER BY {order} LIMIT {int(n)}"

    def _nonsargable_remedy(self) -> str:
        """Engine-specific index type for a leading-'%' LIKE — the ONLY dialect-specific bit of
        causal_regression_hint. Overridden per engine so PG's 'trigram/GIN' never leaks into the
        MySQL model's prompt (and vice-versa). Neutral default names neither."""
        return "a specialized text-search index"

    def is_seeded_a_shape(self, sql: str) -> bool:
        """True when `sql` matches a pattern that already has a HAND-SEEDED architecture-A transform
        (currently: the correlated-scalar-aggregate decorrelation shape). The promotion uses this to
        SKIP re-promoting a discovery that is ALREADY formalized as deterministic A — its B rule was
        retired (A intercepts before free-form), so re-creating it would re-introduce the prompt bias we
        removed. Keys on the SAME structural recognizer A is gated on, so the same discovery from ANY
        engine (PG and MySQL share the decorrelation shape) is caught once. False on parse failure."""
        try:
            from src.pipeline.backends.relational.sql_rewriter import detect_correlated_scalar_aggregate
            return detect_correlated_scalar_aggregate(sql, self.sqlglot_dialect()) is not None
        except Exception:
            return False

    def sanitize_corrector_errors(self, errors: list[str]) -> list[str]:
        """Privacy guard: forward ONLY structured validator messages to the coder — never raw query
        results or arbitrary DB exception text. The coder sees query TEXT + known markers (column/
        table names, predicate strings), never a string that could carry table DATA."""
        safe = []
        for e in errors or []:
            low = (e or "").lower()
            if any(m in low for m in _SQL_MECHANICAL_MARKERS) or "integrity check" in low:
                safe.append(e)
        return safe

    def build_corrector_prompt(self, original: str, broken: str, errors: list[str], history: str = "", cot: bool = False, schema_context: str = "") -> str:
        """The SQL-specialist corrector prompt (few-shot + anti-patterns). Engine-specific: a NoSQL
        backend supplies its own specialist prompt here without touching the corrector node.
        `cot=True` = degrau 3 (CoT). `schema_context` = degrau schema-linking: the retrieved REAL columns
        of the relevant tables, injected so the coder stops hallucinating column names (the dominant
        WRITE-duro error). Empty string = baseline (off)."""
        return _SQL_CORRECTOR_PROMPT.format(
            original=original, broken=broken, error="\n".join(self.sanitize_corrector_errors(errors)), history=history,
            tail=(_CORRECTOR_TAIL_COT if cot else _CORRECTOR_TAIL_DIRECT), schema=schema_context,
        )

    def schema_catalog(self) -> dict:
        """{table_name: [(col_name, type), ...]} for schema-linking (retrieval). Built from extract_schema
        (engine-agnostic). The retriever embeds these and injects the relevant ones into the coder prompt.
        Memoizado por processo (schema é estático durante a run; evita N× COUNT(*) do extract_schema quando
        o linter chama por tentativa de writer)."""
        if getattr(self, "_schema_catalog_memo", None) is None:
            tabs, _ = self.extract_schema()
            self._schema_catalog_memo = {t["name"]: [(c["name"], c.get("type", "")) for c in t.get("columns", [])] for t in tabs}
        return self._schema_catalog_memo

    def lint_self_consistency(self, sql: str) -> tuple[bool, str]:
        """Linter DETERMINÍSTICO pré-execução p/ erros AUTO-infligidos que o writer comete mesmo com o schema
        todo (observados: CTE-fantasma `customer_total_return_temp`; coluna `ctr1.ctr_state` não produzida pelo
        próprio CTE; referência ambígua `ctr_total_return`). Devolve (ok, mensagem cirúrgica). AGNÓSTICO (sqlglot
        + schema_catalog do banco conectado). FAIL-OPEN: qualquer incerteza → (True, "") e deixa o DB + S=1 julgar
        (um falso-positivo só custa uma re-tentativa do writer, nunca aceita SQL errado). Backstop: S=1."""
        try:
            ast = sqlglot.parse_one(sql, dialect=self.sqlglot_dialect())
        except Exception:
            return True, ""  # não-parseável é tratado no fluxo mecânico normal
        cte_names = {c.alias.lower() for c in ast.find_all(exp.CTE) if c.alias}
        try:
            cat = {t.lower(): {c.lower() for c, _ in cols} for t, cols in self.schema_catalog().items()}
        except Exception:
            cat = {}
        # CHECK 1 (alta precisão): fonte indefinida — tabela em FROM/JOIN que não é base nem CTE definido
        if cat:
            for tbl in ast.find_all(exp.Table):
                nm = (tbl.name or "").lower()
                if nm and nm not in cat and nm not in cte_names:
                    return False, (f"you reference `{tbl.name}`, which is neither a base table nor a CTE you "
                                   f"defined. Define it as a CTE (WITH ...) or use the correct table name.")
        # CHECK 2 (ambíguo / coluna fora de escopo) via sqlglot.qualify — guardado, fail-open.
        # SELECT * de PROJEÇÃO → resolução de colunas incompleta, não dá p/ checar com segurança.
        # ⚠️ NÃO confundir com COUNT(*): o `*` de função NÃO é projeção e não deve cegar o check
        # (senão toda query com COUNT(*) — meia TPC-DS — escapa do lint). Só star direto na projeção.
        def _has_projection_star(node) -> bool:
            for sel in node.find_all(exp.Select):
                for e in sel.expressions:
                    if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star)):
                        return True
            return False
        if _has_projection_star(ast):
            return True, ""
        if not cat:
            return True, ""
        try:
            from sqlglot.optimizer.qualify import qualify
            sch = {t: {c: (typ or "UNKNOWN") for c, typ in cols} for t, cols in self.schema_catalog().items()}
            qualify(ast.copy(), schema=sch, dialect=self.sqlglot_dialect(), validate_qualify_columns=True)
        except Exception as e:
            m = str(e).lower()
            if "ambiguous" in m:
                return False, (f"ambiguous column reference — qualify EVERY column with its table/alias "
                               f"(e.g. t.col). Detail: {str(e)[:120]}")
            # sqlglot 30.x: "could not be resolved" cobre coluna desconhecida E ambígua (resolve falhando)
            if any(k in m for k in ("could not be resolved", "unknown column", "cannot automatically join", "not found")):
                return False, (f"a column could not be resolved — it is NOT produced by any in-scope table/CTE, "
                               f"OR it is ambiguous (present in >1 source). Make sure the CTE/table that should "
                               f"produce it SELECTs it, and qualify it with its alias. Detail: {str(e)[:140]}")
            return True, ""  # qualquer outro erro do otimizador → fail-open
        return True, ""

    def precheck_semantic_preservation(self, original: str, rewrite: str) -> tuple[bool, str]:
        """Pré-check FORMAL de preservação semântica (`[4]`, opt-in `SEMANTIC_PRECHECK`) — barra ANTES de
        executar as reescritas provadamente NÃO-equivalentes. Diferente do `lint_self_consistency` (que só
        olha a reescrita e pergunta "é auto-consistente?"), este COMPARA original × reescrita e pergunta
        "preservou o que não podia mudar?".

        Os 3 modos são DERIVADOS dos 12 `equivalence_failed` medidos no aided-MySQL (2026-07-19) — não
        inventados. Todos são erros de RACIOCÍNIO do coder, agnósticos de engine (o cloud potente comete):
          M1  LIMIT atravessando fronteira de agregação — o coder copia o LIMIT externo p/ dentro de uma
              CTE/subquery (q96: LIMIT dentro do derived → COUNT(*) conta 100; q38: LIMIT em cada ramo do
              INTERSECT). Regra: LIMIT em escopo INTERNO que o original não tinha em escopo interno.
          M2a tabela-base DERRUBADA — join do original ausente na reescrita (q63: perdeu `store`).
          M2b auto-join COLAPSADO — tabela usada em DOIS papéis no original (ex.: endereço-de-devolução na
              CTE × endereço-atual no outer) reduzida a UM na reescrita → o predicado externo passa a
              filtrar o papel errado (q30). Só dispara quando o original REALMENTE a usa 2+ vezes.
          M3  window-sobre-agregado ACHATADA — `AVG(SUM(x)) OVER (...)` vira `AVG(x)` com GROUP BY, o que
              muda o NÍVEL de agregação (q63: média dos preços brutos ≠ média das somas mensais).

        Devolve (ok, mensagem cirúrgica) no mesmo contrato do lint. **FAIL-OPEN e conservador por projeto:**
        é lever de EFICIÊNCIA — deve barrar cedo o que o S=1 barraria depois, NUNCA mudar o land-set. Um
        falso-positivo custa uma re-tentativa do writer; o S=1 segue como autoridade final."""
        d = self.sqlglot_dialect()
        try:
            o_ast = sqlglot.parse_one(original, dialect=d)
            r_ast = sqlglot.parse_one(rewrite, dialect=d)
        except Exception:
            return True, ""  # não-parseável → fluxo mecânico normal julga

        def _inner_limits(ast) -> int:
            """LIMIT que NÃO está no SELECT raiz (o LIMIT de topo é legítimo e quase universal na TPC-DS)."""
            root = ast.find(exp.Select)
            return sum(1 for s in ast.find_all(exp.Select) if s is not root and s.args.get("limit"))

        # M1 — LIMIT empurrado p/ escopo interno que o original não tinha. Alta precisão: só dispara quando
        # o original tem ZERO limits internos e a reescrita tem ≥1 (introdução pura, não movimentação).
        if _inner_limits(r_ast) > 0 and _inner_limits(o_ast) == 0:
            return False, (
                "you introduced a LIMIT inside a subquery/CTE. The ORIGINAL has no inner LIMIT — truncating "
                "an inner scope changes the rows that reach the aggregate/set-operation above it, so the "
                "result is NOT equivalent. Keep LIMIT only where the ORIGINAL has it (the outermost query)."
            )

        def _tbl_counts(ast) -> dict[str, int]:
            """Quantas vezes cada tabela BASE é referenciada (CTEs definidos aqui não contam como base)."""
            ctes = {c.alias.lower() for c in ast.find_all(exp.CTE) if c.alias}
            out: dict[str, int] = {}
            for t in ast.find_all(exp.Table):
                nm = (t.name or "").lower()
                if nm and nm not in ctes:
                    out[nm] = out.get(nm, 0) + 1
            return out

        o_tbl, r_tbl = _tbl_counts(o_ast), _tbl_counts(r_ast)
        # M2a — tabela-base do original SUMIU. Derrubar um join quase sempre multiplica/solta linhas.
        missing = sorted(set(o_tbl) - set(r_tbl))
        if missing:
            return False, (
                f"your rewrite DROPPED the table(s) {', '.join('`'+m+'`' for m in missing)} that the ORIGINAL "
                f"joins. Removing a join changes the result set (rows multiply or filters vanish). Keep EVERY "
                f"base table of the ORIGINAL, with its join condition."
            )
        # M2b — auto-join colapsado: a tabela aparecia em 2+ PAPÉIS no original e virou 1 na reescrita.
        for t, n in o_tbl.items():
            if n >= 2 and r_tbl.get(t, 0) < n:
                return False, (
                    f"the ORIGINAL references `{t}` {n} times — these are DIFFERENT roles (each occurrence is "
                    f"joined on a different key and may carry its own filter). Your rewrite collapses it to "
                    f"{r_tbl.get(t, 0)}, so a predicate now filters the WRONG occurrence. Keep each occurrence "
                    f"of `{t}` separate, with the predicate attached to the SAME occurrence as in the ORIGINAL."
                )

        # ❌ M3 (window-sobre-agregado achatada) foi IMPLEMENTADO E REMOVIDO — registro do porquê, p/ não
        # voltar: a regra "original tem AVG(SUM(x)) OVER(...) e a reescrita não tem window ⇒ mudou o nível de
        # agregação" deu FALSO-POSITIVO num LAND REAL (TPC-DS q63, +80%, S=1 ✓ nos dois stores MySQL). Achatar
        # a window PODE ser correto — a agregação em dois passos explícita (GROUP BY + JOIN no nível de cima)
        # é justamente a decorrelação válida. O erro do q63-que-falhou não foi achatar, foi achatar no nível
        # ERRADO (média dos preços brutos × média das somas), e isso NÃO é estaticamente separável do caso
        # correto sem resolver a semântica do GROUP BY. Fica com o S=1. Contrato do lever = zero falso-positivo.
        return True, ""

    def dialect_rule_map(self) -> list[tuple[tuple[str, ...], str]]:
        """Mapa (assinatura-do-erro → regra corretiva) do engine. Vazio na base; cada engine sobrescreve com
        o que o SEU parser rejeita. A assinatura são tokens do erro DO PRÓPRIO SGBD (código + trecho da
        mensagem), NUNCA algo do nosso benchmark — é isso que torna o disparo reativo ao engine."""
        return []

    def dialect_rules(self, errors: list[str] | None = None) -> str:
        """Regras de DIALETO p/ o writer — o lever D (`DIALECT_HINTS`). NÃO confundir com `syntax_hint()`,
        que é parity de BASELINE (estrutura da linguagem, dada a TODOS os braços): estas são regras
        CORRETIVAS, e só entram com o lever ligado.

        DOIS MODOS, porque medem coisas diferentes (ver next_steps → "Degraus do D"):
          `on`      REATIVO (degrau 2, default) — injeta SÓ a regra cuja assinatura de erro o ENGINE acabou
                    de emitir. Não assume quais erros vão aparecer: o gatilho é o SGBD, não a nossa amostra
                    de 21 queries. Na 1ª tentativa (sem erro ainda) não injeta nada — a regra entra no retry,
                    que é exatamente onde o writer tem informação pra usar.
          `blanket` TETO (degrau 1) — injeta TODAS as regras sempre. ⚠️ ENVIESADO por construção: a lista foi
                    escrita a partir dos erros observados AQUI, então é o caso mais generoso possível. Serve
                    p/ medir o TETO ("conhecimento de dialeto vale alguma coisa?"), NÃO é implantável. Se nem
                    o blanket mover os `mechanics`, o ramo inteiro morre barato.
        Nenhum dos dois é a resposta de produção — essa é o degrau 3 (RAG sobre a doc oficial do SGBD, [1c]),
        que cobre código de erro que nunca vimos."""
        rules = self.dialect_rule_map()
        if not rules:
            return ""
        mode = os.getenv("DIALECT_HINTS", "off").strip().lower()
        if mode == "blanket":
            picked = [r for _, r in rules]
        else:
            blob = " ".join(str(e) for e in (errors or [])).lower()
            if not blob:
                return ""
            picked = [r for sig, r in rules if any(tok.lower() in blob for tok in sig)]
        if not picked:
            return ""
        return (
            "Dialect rules for this database — your previous rewrite violated these:\n"
            if mode != "blanket" else
            "Dialect rules for this database — your rewrite MUST obey these:\n"
        ) + "".join(picked)

    def build_strategy_decision_prompt(self, raw_query: str, explain: dict, hw_hints: dict, prev_strategy: str | None = None, prev_why: str | None = None, schema_context: str = "") -> str:
        """Modo 2-agentes: o architect DECIDE a estratégia (lista NL-estruturada, sem SQL) a partir do PLANO.
        Específico (target exato + transformação). Em re-estratégia, recebe a anterior + por que falhou.
        `schema_context` (B-find, opt-in SCHEMA_LINKING_FIND): colunas reais + join-keys → o FIND mira melhor
        (targeting de coluna/chave), não só o WRITE. Vazio = FIND decide só do PLANO (comportamento antigo)."""
        # ⚠️ REESCRITO 29/08 (pedido da usuária: "esse prompt tá muito mal escrito... tira esse plan
        # hints e fala olhe pra consulta e pro schema e pronto"). O texto anterior era:
        #     "Execution plan unavailable: withheld for ablation (PLAN_HINTS=off) — optimize from the
        #      query STRUCTURE + schema only"
        # Dois defeitos: (1) prometia um **schema** que, com SCHEMA_LINKING=off, NÃO ia no prompt —
        # instrução apontando para algo ausente, que num 8B plausivelmente puxa alucinação de coluna
        # (o `mechanics_failed` é 63-69% nas células de writer local); (2) vazava vocabulário do
        # experimento ("ablation", "PLAN_HINTS") para dentro da tarefa do modelo.
        # Agora: quando o plano é retido, o bloco fica VAZIO — o prompt simplesmente não fala de plano.
        plan = (f"Physical Execution Plan:\n{explain['plan']}\n\n" if not explain.get("error") else "")
        # PLAN_HINTS ablation: withheld plan → swap the plan-reading instructions for STRUCTURE-only ones
        # (same non-prescriptive discipline). ON-PATH (plan present) is BYTE-IDENTICAL to before.
        # ANTI-SEEDING (2026-07-07): the strategy prompt must be as NEUTRAL as the raw build_fallback_prompt.
        # Previously it named "correlated subqueries" AND gave "the scalar avg(...) subquery correlated on
        # <column>" as an example — literally q1's pattern → it SEEDED the decorrelation instead of measuring
        # discovery (the aided arm landed q1 because the prompt told it what to look for; the raw prompt does
        # not). Now the find_line mirrors the raw prompt's non-prescriptive evidence-reading (which OPERATIONS
        # are expensive / query-shape cost drivers), naming no technique or pattern → symmetric with raw.
        if explain.get("error"):
            # ⚠️ O que o modelo é mandado LER tem de bater com o que ele RECEBE. Sem plano, a fonte é a
            # query — e o schema SÓ é citado se o bloco de schema estiver de fato no prompt.
            _has_schema = bool(schema_context)
            read_src = "the QUERY and its SCHEMA" if _has_schema else "the QUERY"
            find_line = ("identify what likely drives the cost from the query shape (subqueries evaluated "
                         "repeatedly, large joins, repeated scans of the same table)")
            from_src = "the query and schema" if _has_schema else "the query"
        else:
            read_src = "the EXECUTION PLAN"
            find_line = ("identify the operations driving the cost in the plan (full scans on large tables; "
                         "nested loops with a large outer row count; operations where the estimate diverges "
                         "sharply from the actual; the operations that dominate the total cost)")
            from_src = "the plan"
        hw = ""
        if hw_hints:
            _l = ["Hardware context:"]
            if hw_hints.get("cache_hit_ratio_pct") is not None:
                r = hw_hints["cache_hit_ratio_pct"]
                _l.append(f"  Buffer cache hit ratio: {r}% — {'random I/O cheap' if r >= 90 else 'disk I/O expensive'}")
            if hw_hints.get("work_mem_mb") is not None:
                _l.append(f"  Work memory: {hw_hints['work_mem_mb']:.1f} MB per sort/hash")
            if hw_hints.get("random_page_cost") is not None and hw_hints.get("seq_page_cost"):
                rr = hw_hints["random_page_cost"] / hw_hints["seq_page_cost"]
                _l.append(f"  Random/Seq I/O cost: {rr:.1f}x — {'index scans cheap' if rr <= 2 else 'prefer seq scans'}")
            hw = "\n".join(_l) + "\n\n"
        db = f"Database: {self.db_version()}\n" if self.db_version() else ""
        prev = ""
        if prev_strategy:
            prev = (f"\nThe PREVIOUS strategy did NOT work ({prev_why or 'no gain / could not be written'}):\n"
                    f"{prev_strategy}\nPropose a DIFFERENT strategy — another technique OR another target.\n")
        # GUIDED arm (hint-B, APPLY_HEURISTICS on): inject the VALIDATED, GENERAL discovered techniques so the
        # architect CONSIDERS them when the query's shape matches. EMPTY in discovery mode (learned_rules_text
        # returns "" unless application mode) → the strategy prompt stays neutral/unseeded, symmetric with raw.
        from src.pipeline.discovery.learned_rules import learned_rules_text
        _learned = learned_rules_text()
        learned = (
            "\nValidated techniques discovered on other queries — CONSIDER each when THIS query's shape "
            "matches (the result set MUST stay identical; if none applies, ignore them):\n" + _learned + "\n"
            if _learned else ""
        )
        # rewrite-focus (RW, opt-in REWRITE_FOCUS): scope the FIND to REWRITE only. Without it the architect
        # deflects to "add an index" as a strategy — but the coder writes SQL, not DDL → it can't act → no_gain
        # (measured on MySQL M3: perde q18/q25/q69 pro desvio-pro-índice). Indexes are a SEPARATE advisor's job
        # (index_advisor), already gated in the router. Agnostic (shared PG+MySQL prompt); names no technique.
        rwfocus = (
            "- Do NOT propose adding indexes, DDL, or \"CREATE INDEX\" — the rewrite is SQL-only, and indexes "
            "are handled by a SEPARATE advisor. Propose ONLY query-REWRITE (equivalence-preserving) "
            "transformations.\n"
            if _flag("REWRITE_FOCUS") else ""
        )
        schema = (f"Relevant table columns (real schema — use the EXACT column/key names):\n{schema_context}\n\n"
                  if schema_context else "")
        return _SQL_STRATEGY_DECISION_PROMPT.format(db=db, hw=hw, plan=plan, original=raw_query, prev=prev,
                                                    read_src=read_src, find_line=find_line, from_src=from_src,
                                                    learned=learned, rwfocus=rwfocus, schema=schema)

    def build_strategy_prompt(self, original: str, broken: str) -> str:
        """Degrau 4: the FIND-agent (reasoning model) articulates the botch's optimization strategy IN WORDS
        (no SQL) — QUITE's first agent. Fed to build_generate_prompt so the coder writes fresh from the
        strategy instead of patching the broken SQL."""
        return _SQL_STRATEGY_PROMPT.format(original=original, broken=broken)

    def build_generate_prompt(self, original: str, strategy: str, errors: list[str], history: str = "", cot: bool = False, schema_context: str = "") -> str:
        """Degrau 4: the WRITE-agent (coder) writes the rewrite FROM SCRATCH given (original + strategy) —
        does NOT see the broken SQL, so it can't inherit its mess. `cot` adds the reason-then-write tail.
        `schema_context` = schema-linking (colunas reais das tabelas) p/ o writer não alucinar coluna."""
        err = "\n".join(self.sanitize_corrector_errors(errors))
        # ENGINE PARITY (fix 2026-07-03): the WRITER writes the SQL, so it MUST get the same dialect
        # scaffolding the solo/raw architect had when IT wrote SQL — db_version + syntax_hint. Before, this
        # was stranded on the architect (who only DECIDES) → the writer was blind to the engine (would guess
        # the dialect / recover only via the error loop). This is BASELINE parity, NOT the `+dialecthints`
        # lever (no "cannot do FILTER→CASE" rules here — those stay a separate aided-local A/B).
        _db = self.db_version()
        dialect = (f"Target database: {_db}\n" if _db else "") + self.syntax_hint() + "\n\n"
        # Lever D: regras corretivas do engine, DEPOIS do syntax_hint de baseline. No modo REATIVO (default)
        # `errors` é o gatilho — só entra a regra cuja assinatura o SGBD acabou de emitir; sem erro, nada.
        if os.getenv("DIALECT_HINTS", "off").strip().lower() not in ("", "off", "0", "false", "no"):
            _dr = self.dialect_rules(errors)
            if _dr:
                dialect += _dr + "\n"
        # Lever SEM (`SEMANTIC_GUARD=on`): checklist de PRESERVAÇÃO no WRITE — espelho do REWRITE_FOCUS (que
        # escopa o FIND). Derivado dos modos de não-equivalência MEDIDOS no aided-MySQL (os 12 equiv-fail,
        # 2026-07-19): LIMIT atravessando agregação (q96/q38), predicado empurrado p/ a ocorrência errada de
        # uma tabela reusada (q30/q81), join derrubado (q63). São erros de RACIOCÍNIO, agnósticos de engine —
        # por isso o texto não nomeia engine, benchmark, tabela nem técnica.
        # ⚠️ ESCOPO DELIBERADO — só o modo M1 (LIMIT interno). Os outros modos medidos (M2 predicado na
        # ocorrência errada de tabela reusada · OR→UNION ALL · M3 nível de agregação) foram RETIRADOS do
        # checklist de propósito: a auditoria de `adherence` mostrou 12/12 dos equiv-fail marcados ADHERENT,
        # e várias ESTRATÉGIAS do architect prescreviam literalmente a transformação que quebrou (q30/q81
        # "Filter customer_address Early in CTE"; q63/q85 "Split the OR condition"). Ou seja: o erro nasce no
        # FIND, e o coder obedeceu. Instruir o coder a DESOBEDECER a estratégia contaminaria a medição do
        # FIND do modelo leve (um land futuro seria "o 8B achou" ou "o coder cloud consertou o 8B"?) — que é
        # justamente a claim que o Paper 1 sustenta. Esses modos ficam como LIMITAÇÃO MEDIDA do FIND.
        # O M1 não tem esse conflito: nenhuma estratégia pediu LIMIT — é desleixo puro do WRITE.
        semguard = (
            "\nPRESERVATION CHECKLIST — the rewrite is only valid if this holds:\n"
            "- Do NOT add a LIMIT anywhere the ORIGINAL does not have one. A LIMIT inside a subquery/CTE "
            "truncates the rows feeding an aggregate or set-operation above it and changes the result.\n"
            if _flag("SEMANTIC_GUARD") else ""
        )
        return _SQL_GENERATE_PROMPT.format(
            dialect=dialect, semguard=semguard,
            original=original, strategy=strategy, schema=("\n" + schema_context if schema_context else ""),
            error=(f"\nThe previous rewrite was rejected:\n{err}\n" if err else ""),
            history=(f"\nPrevious attempts (do not repeat):\n{history}\n" if history else ""),
            tail=(_CORRECTOR_TAIL_COT if cot else _CORRECTOR_TAIL_DIRECT),
        )

    def build_adherence_prompt(self, strategy: str, original: str, rewrite: str) -> str:
        """Juiz de ADERÊNCIA (LLM-as-judge): o writer escreveu a TÉCNICA que o architect DECIDIU, ou
        re-otimizou por conta própria? Protege a atribuição do teto (FIND-qwen / WRITE-cloud). Ignora ops
        de índice/DDL da estratégia (o writer não cria índice). Saída: 1ª linha ADHERENT|DEVIATED + razão."""
        return _SQL_ADHERENCE_PROMPT.format(strategy=strategy, original=original, rewrite=rewrite)

    def relevant_schema_block(self, *sqls: str) -> str:
        """Schema-linking DETERMINÍSTICO (sem embedding): das tabelas referenciadas nos SQLs dados —
        (1) as COLUNAS reais (o que EXISTE → ataca alucinar/renomear coluna) + (2) as JOIN KEYS / FKs
        ENTRE essas tabelas (COMO conectá-las → ataca erro de ESCOPO/colocação de join, ex. q30 jogou
        ca_state num CTE sem caminho até customer_address). Uma única chamada a extract_schema (usa os
        DOIS retornos: tabelas + relations). Vazio se não parsear ou não houver tabela conhecida."""
        try:
            tabs_meta, rels = self.extract_schema()
        except Exception:
            return ""
        cat = {t["name"]: [(c["name"], c.get("type", "")) for c in t.get("columns", [])] for t in tabs_meta}
        involved = set()
        for s in sqls:
            involved |= (self._base_tables(s) or set())
        cols = [f"- {t}(" + ", ".join(c for c, _ in cat[t]) + ")" for t in sorted(involved) if t in cat]
        if not cols:
            return ""
        block = "AVAILABLE COLUMNS (these exist — use ONLY these, never invent a column):\n" + "\n".join(cols)
        # JOIN KEYS: só arestas em que AMBAS as tabelas estão na query (não poluir com o schema inteiro)
        edges, seen = [], set()
        for r in rels:
            ft, tt = r.get("from_table"), r.get("to_table")
            if ft in involved and tt in involved:
                for fc, tc in zip(r.get("from_columns", []), r.get("to_columns", [])):
                    key = tuple(sorted([f"{ft}.{fc}", f"{tt}.{tc}"]))
                    if key not in seen:
                        seen.add(key)
                        edges.append(f"- {ft}.{fc} = {tt}.{tc}")
        if edges:
            block += ("\n\nJOIN KEYS (use these to connect the tables — to reference a column from a table, "
                      "that table MUST be joined via its key):\n" + "\n".join(sorted(edges)))
        return block

    def corrector_preserved_strategy(self, original: str, broken: str, repaired: str) -> bool:
        """PROVE the corrector only REPAIRED — it did not change the optimization STRATEGY. Uses the SET of
        tokens (Jaccard overlap), NOT sequence order: a mechanical fix — even one that RESTRUCTURES (e.g.
        CROSS JOIN LATERAL -> derived table in FROM) — keeps the SAME tables/columns/aggregates, only
        reordered, so the token SET barely changes. A re-optimization into a DIFFERENT transform uses
        DIFFERENT tokens -> low overlap -> rejected. (Sequence similarity wrongly punished legit
        restructures, which is why the corrector stopped rescuing q9 — observed 2026-06-18.) A revert to
        the original is caught separately in the node. Tunable CORRECTOR_MIN_SIMILARITY (default 0.5);
        backstopped by S=1 + manual review. Fail-open when nothing to compare."""
        toks = lambda s: set(re.findall(r"[a-z0-9_]+", (s or "").lower()))
        b, r = toks(broken), toks(repaired)
        if not b or not r:
            return True
        jaccard = len(b & r) / len(b | r)
        return jaccard >= float(os.getenv("CORRECTOR_MIN_SIMILARITY", "0.5"))

    def _base_tables(self, sql: str) -> frozenset | None:
        """The SET of physical base tables referenced (CTE names excluded). None on parse failure.
        A technique-preserving repair touches the SAME base tables; a different transform pulls in
        (or drops) tables. Engine-agnostic, no benchmark knowledge."""
        try:
            ast = sqlglot.parse_one(sql, dialect=self.sqlglot_dialect())
            ctes = {c.alias.lower() for c in ast.find_all(exp.CTE)}
            return frozenset(t.name.lower() for t in ast.find_all(exp.Table) if t.name.lower() not in ctes)
        except Exception:
            return None

    def technique_preserved(self, broken: str, repaired: str, original: str | None = None) -> tuple[bool | None, str]:
        """STRUCTURAL (AST) technique-signature check — the strong complement to the token-Jaccard
        gate. Instead of asking "do the two share most tokens?", it asks "is it the SAME technique,
        and did the coder actually FIX it (vs throw it away)?" via three checks that survive a legit
        MECHANICAL restructure but break on a re-optimization or a revert:

          1. SAME base tables (a different transform adds/drops tables).
          2. The repair did NOT *introduce* a correlated subquery the botch lacked. Correlation is
             ASYMMETRIC on purpose: a legit restructure (CROSS JOIN LATERAL -> derived table) REMOVES
             correlation — that must pass — but RE-correlating an already-decorrelated rewrite means
             the coder undid the FIND (re-optimized to a worse shape) — that must fail. So the rule is
             `corr(repair) and not corr(botch)` -> reject; removing correlation is always allowed.
          3. REVERT-GUARD (only when `original` is given): a coder that reaches S=1 by RECONSTRUCTING
             the original query — not by fixing the model's botched technique — does NOT validate the
             FIND (the model's transform is gone; the "rescue" is the coder's own baseline). Caught by
             sequence similarity: if the repair is MORE similar to the original than to the botch
             (margin 0.05), the coder reverted/reconstructed. (Manual audit 2026-06-26, qwen: q51 and
             q63 reached S=1 purely by rebuilding the original CTE/comma-join shape; the token-Jaccard
             gate and the tables+correlation checks were both blind to it — only this guard catches it.)
             Byte-identical reverts are already caught upstream in the probe; this catches CLEANED ones.

        Returns (verdict, reason). verdict is None (fail-open) when either side does not parse — the
        Jaccard gate + S=1 still apply; this check only ever ADDS confidence, never silently passes a
        re-optimization. Validated 2026-06-26 on synthetic pairs (alias-fix PASS, re-correlation FAIL,
        LATERAL->derived PASS, table-swap FAIL) + the qwen audit sample (q51/q63 reverts FAIL)."""
        tb, tr = self._base_tables(broken), self._base_tables(repaired)
        if tb is None or tr is None:
            return None, "não-parseável (um dos lados) → fallback Jaccard"
        if tb != tr:
            return False, f"tabelas mudaram (±{sorted(tb ^ tr)}) → técnica diferente"
        if self.has_correlated_subquery(repaired) and not self.has_correlated_subquery(broken):
            return False, "reparo ADICIONOU correlação → re-otimizou (FIND mudou)"
        if original:
            import difflib
            _n = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())
            sim_botch = difflib.SequenceMatcher(None, _n(repaired), _n(broken)).ratio()
            sim_orig = difflib.SequenceMatcher(None, _n(repaired), _n(original)).ratio()
            if sim_orig > sim_botch + 0.05:
                return False, (f"reparo mais perto do ORIGINAL ({sim_orig:.2f}) que do botch ({sim_botch:.2f}) "
                               f"→ reverteu/reconstruiu (técnica do modelo NÃO preservada)")
        return True, "técnica preservada (mesmas tabelas; não re-correlacionou; não reverteu)"

    def equivalence_diff(self, ref_rows: list[str] | None, cand_rows: list[str] | None) -> str | None:
        """Degrau 2 (`RICH_EQUIVALENCE_FEEDBACK`): PRIVACY-SAFE rich feedback on an S≠1 failure — tells
        the coder WHAT diverges (cardinality + set-shape) so it can fix the right thing, instead of the
        bare 'returns different rows'. Counterexample-guided repair (Wang et al. 2018, Execution-Guided
        Decoding) WITHIN the privacy pillar: we diff the actual sampled rows IN OUR PROCESS but emit only
        COUNTS and a structural CATEGORY — never a single data value leaves. Both samples are the same
        ORDER BY-all-cols LIMIT-100 basis as sample_hash, so the diff is on exactly what S=1 compared.
        The message starts with the 'result-set mismatch' marker so sanitize_corrector_errors forwards it.
        Returns None when either sample is missing (caller falls back to the generic message)."""
        if ref_rows is None or cand_rows is None:
            return None
        n_ref, n_cand = len(ref_rows), len(cand_rows)
        ref_set, cand_set = set(ref_rows), set(cand_rows)
        missing = len(ref_set - cand_set)   # estão no original, sumiram no reparo
        extra = len(cand_set - ref_set)     # apareceram no reparo, não no original
        dup_ref, dup_cand = n_ref - len(ref_set), n_cand - len(cand_set)
        head = (f"result-set mismatch (S≠1) — amostra ordenada de até 100 linhas: "
                f"original={n_ref}, sua reescrita={n_cand}.")
        if missing and extra:
            why = (f"{missing} linha(s) do original SUMIRAM e {extra} A MAIS apareceram → conteúdo divergente "
                   f"(predicado, chave de JOIN ou projeção/colunas erradas).")
        elif extra and not missing:
            why = (f"{extra} linha(s) A MAIS, nenhuma sumiu → você retorna linhas DEMAIS "
                   f"(filtro faltando, JOIN multiplicando, ou falta DISTINCT/dedup).")
        elif missing and not extra:
            why = (f"{missing} linha(s) SUMIRAM, nenhuma a mais → você retorna linhas DE MENOS "
                   f"(predicado restritivo demais, INNER onde devia ser OUTER JOIN, ou GROUP BY a mais).")
        elif dup_cand != dup_ref:
            why = (f"as linhas distintas batem mas a MULTIPLICIDADE difere "
                   f"(duplicatas: original {dup_ref}, você {dup_cand}) → dedup/cardinalidade do JOIN.")
        else:
            why = "linhas distintas iguais mas contagem difere → ordenação/limite/duplicatas."
        return f"{head} {why} Preserve EXATAMENTE o mesmo result-set (mesmas linhas e multiplicidade); NÃO mude a estratégia."

    def extract_query(self, text: str) -> str:
        # Reasoning models (phi4-mini-reasoning, qwen3, deepseek-r1) wrap chain-of-thought in
        # <think>...</think>. That prose can itself start with "with"/"select" and fool the
        # bare-statement fallback below into returning reasoning instead of SQL — strip it first.
        text = re.sub(r"<think>.*?</think>", " ", text, flags=re.DOTALL | re.IGNORECASE)

        def _parses(sql: str) -> bool:
            try:
                sqlglot.parse_one(sql, dialect=self.sqlglot_dialect())
                return True
            except Exception:
                return False

        # 1) Fenced blocks are the strongest signal (how qwen3/deepseek-r1 emit SQL). Prefer the
        #    LAST block that parses — the final answer follows any earlier scratch/draft blocks.
        fenced = re.findall(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        for block in reversed(fenced):
            block = block.strip().rstrip(";")
            if block and _parses(block):
                return block

        # 2) No usable fence: scan for SELECT/WITH statements, but only accept a candidate that
        #    actually parses — rejects reasoning prose like "with date_dim for year 2000...".
        matches = [m.group(1) for m in re.finditer(
            r"((?:SELECT|WITH)\b.*?)(?:\n\s*\n|\n###|\n##|\n\*\*|\Z)", text,
            re.DOTALL | re.IGNORECASE)]
        for cand in reversed(matches):
            cand = cand.strip().rstrip(";")
            if cand and _parses(cand):
                return cand

        # 3) Nothing parsed — keep the legacy best-effort so models that already work never
        #    regress (the caller validates the SQL downstream regardless).
        if fenced:
            return fenced[0].strip().rstrip(";")
        if matches:
            return matches[0].strip().rstrip(";")
        return text.strip().rstrip(";")

    def repair_presentation_clauses(self, raw_query: str, optimized_query: str) -> tuple[str, list[str]]:
        """Re-append top-level ORDER BY / LIMIT / OFFSET that the rewrite silently dropped.

        The result-hash gate (`sample_hash`) deliberately strips ORDER BY/LIMIT and sorts
        rows in Python before hashing, so it compares the underlying row-set, never the
        presentation. A rewrite can therefore drop the user's ORDER BY/LIMIT and still pass
        the hash — but those clauses are part of the query's output contract (ordered, top-N).
        We restore any top-level clause the original had and the optimized is missing, so the
        served/timed query honours the contract without throwing away an otherwise good rewrite.

        Returns (possibly-repaired sql, list of clause keys restored).
        """
        dialect = self.sqlglot_dialect()
        try:
            orig_ast = sqlglot.parse_one(raw_query, dialect=dialect)
            opt_ast = sqlglot.parse_one(optimized_query, dialect=dialect)
        except Exception:
            return optimized_query, []

        # Only act on top-level SELECT statements (covers WITH ... SELECT, where order/limit
        # live on the outer Select node). Anything else: leave untouched.
        if not isinstance(opt_ast, exp.Select) or not isinstance(orig_ast, exp.Select):
            return optimized_query, []

        restored: list[str] = []
        for key in ("order", "limit", "offset"):
            orig_clause = orig_ast.args.get(key)
            if orig_clause is not None and opt_ast.args.get(key) is None:
                opt_ast.set(key, orig_clause.copy())
                restored.append(key)

        if not restored:
            return optimized_query, []
        return opt_ast.sql(dialect=dialect), restored

    # ── Reflection hints — SQL-flavored advice fed back to the LLM on specific failures.
    # The executor node calls these via getattr and falls back to generic text for backends
    # that don't implement them, so the node itself stays query-language agnostic.

    def hint_no_optimization(self) -> str:
        return (
            "no_change=true — no optimization was found. Try harder: "
            "reorder the join chain more aggressively, choose a more selective anchor table, "
            "or enable CTEs for large filtered tables."
        )

    def hint_integrity_failure(self) -> str:
        return (
            "Integrity check failed: result hashes differ (S≠1) — "
            "your rewrite returns different rows than the original. "
            "Common causes: converting IN/EXISTS to a plain JOIN without DISTINCT multiplies rows; "
            "changing the driving table in FROM alters the result set. "
            "Fix: use EXISTS or add DISTINCT when flattening subqueries into JOINs."
        )

    def hint_plan_identical(self) -> str:
        return (
            "your rewrite produced an execution plan identical to the original — the planner "
            "normalized the change away (e.g. CTE inlining), so there is no effective optimization. "
            "Try a structurally different approach: different join order anchor, MATERIALIZED CTE, "
            "or restructured predicates. If nothing applies, return no_change: true."
        )

    def check_structural_integrity(self, raw_query: str, optimized_query: str) -> list[str]:
        errors = []
        dialect = self.sqlglot_dialect()

        def _split_and(expr) -> list:
            if isinstance(expr, exp.And):
                return _split_and(expr.left) + _split_and(expr.right)
            return [expr]

        def _split_or(expr) -> list:
            if isinstance(expr, exp.Or):
                return _split_or(expr.left) + _split_or(expr.right)
            return [expr]

        def _exists_signatures(cond_node, outer_alias: str) -> set:
            """Extract (inner_tables, corr_col) for each EXISTS node in cond_node.
            Used to compare EXISTS conditions by meaning instead of text — avoids false
            positives when implicit joins inside subqueries are rewritten to explicit JOINs."""
            sigs = set()
            for exists_node in cond_node.find_all(exp.Exists):
                inner_tables = frozenset(t.name.lower() for t in exists_node.find_all(exp.Table))
                corr_col = None
                sub_where = exists_node.find(exp.Where)
                if sub_where:
                    for sc in _split_and(sub_where.this):
                        for col in sc.find_all(exp.Column):
                            if col.table and col.table.lower() == outer_alias:
                                corr_col = col.name.lower()
                                break
                        if corr_col:
                            break
                sigs.add((inner_tables, corr_col))
            return sigs

        try:
            orig_ast = sqlglot.parse_one(raw_query, dialect=dialect)
            opt_ast = sqlglot.parse_one(optimized_query, dialect=dialect)
        except Exception:
            return []

        defined = {t.alias_or_name.lower() for t in opt_ast.find_all(exp.Table)}
        # Derived tables (subqueries in FROM/JOIN) are exp.Subquery nodes, not exp.Table.
        # Their aliases must be included or any column reference to them is a false positive.
        defined |= {s.alias.lower() for s in opt_ast.find_all(exp.Subquery) if s.alias}
        # LATERAL subqueries (e.g. CROSS JOIN LATERAL (...) alias) carry their alias on the
        # exp.Lateral node, not the inner Subquery — include them too (TPC-DS q9 consolidation).
        defined |= {l.alias.lower() for l in opt_ast.find_all(exp.Lateral) if l.alias}
        referenced = {col.table.lower() for col in opt_ast.find_all(exp.Column) if col.table}

        # Diagnostic context for the message: which undeclared aliases are CTEs the rewrite DEFINED
        # but never joined, vs base tables from the ORIGINAL that were dropped. Naming the specific
        # mechanical mistake (symptom) helps the model self-correct on the next attempt — it does NOT
        # prescribe an optimization technique (that stays the model's choice). Same intent as
        # implicit_join_hint / window_filter_hint: fix BROKEN SQL, not choose the strategy.
        orig_cte_names = {c.alias.lower() for c in orig_ast.find_all(exp.CTE) if c.alias}
        opt_cte_names = {c.alias.lower() for c in opt_ast.find_all(exp.CTE) if c.alias}
        orig_tables = {t.name.lower() for t in orig_ast.find_all(exp.Table)} - orig_cte_names
        for alias in sorted(referenced - defined):
            if alias in opt_cte_names:
                errors.append(
                    f"Structural_Error: '{alias}' is a CTE you defined but never joined — you reference "
                    f"'{alias}.<column>' without adding '{alias}' to a FROM/JOIN. Add it to the FROM/JOIN "
                    f"(e.g. JOIN {alias} ON ...) so the columns you read from it resolve, or reference an "
                    f"already-joined source instead."
                )
            elif alias in orig_tables:
                errors.append(
                    f"Structural_Error: '{alias}' is a table from the original query — you reference "
                    f"'{alias}.<column>' but never joined '{alias}'. Every original table must appear in the "
                    f"rewrite; add '{alias}' back to the FROM/JOIN with its join condition from the original."
                )
            else:
                errors.append(f"Structural_Error: alias '{alias}' referenced in query but not declared in FROM/JOIN")
        if errors:
            return errors

        # Exclude CTE names — they are not base tables. Inlining a CTE into a subquery (valid
        # rewrite) makes the CTE name "disappear", which is NOT data loss. Only real base-table
        # removal counts. (orig_cte_names / opt_cte_names / orig_tables computed above.)
        # When the optimized query names a CTE identically to an original base table that is still
        # scanned inside the CTE body (e.g. `WITH t AS (SELECT * FROM t WHERE ...)`), subtracting
        # the CTE name would wrongly drop that still-present base table → false Data_Loss. Only
        # subtract CTE names that are NOT original base tables (genuine inlined/introduced CTEs).
        opt_tables = {t.name.lower() for t in opt_ast.find_all(exp.Table)} - (opt_cte_names - orig_tables)
        for table in sorted(orig_tables - opt_tables):
            errors.append(f"Data_Loss_Detected: table '{table}' was removed from FROM/JOIN")

        # Operator-precedence guard. When the model splits a parenthesized OR-group
        # (e.g. "(col LIKE x OR col LIKE y OR col LIKE z)") into UNION ALL branches,
        # it can drop the parentheses and write "... AND col LIKE x OR col LIKE y" —
        # which parses as "(... AND x) OR y" because AND binds tighter than OR. For
        # aggregate queries (MIN/MAX/COUNT) the result-hash gate can't catch this (the
        # aggregate is insensitive to the changed row set), so we check it structurally.
        def _leaf_key(node) -> str:
            return re.sub(r'\b\w+\.', '', node.sql(dialect=dialect).lower())

        # Predicates that, in the ORIGINAL, lived inside a parenthesized OR-group.
        or_protected = set()
        for or_node in orig_ast.find_all(exp.Or):
            for leaf in _split_or(or_node):
                if not isinstance(leaf, (exp.Or, exp.And)):
                    or_protected.add(_leaf_key(leaf))

        if or_protected:
            for and_node in opt_ast.find_all(exp.And):
                operands = _split_and(and_node)
                for op in operands:
                    if isinstance(op, (exp.And, exp.Or)) or _leaf_key(op) not in or_protected:
                        continue
                    op_cols = {c.name.lower() for c in op.find_all(exp.Column)}
                    # An OR-group leaf must never be AND-ed directly with a predicate on a
                    # different column — that means it lost its parentheses.
                    for sib in operands:
                        if sib is op:
                            continue
                        sib_cols = {c.name.lower() for c in sib.find_all(exp.Column)}
                        if sib_cols and not (sib_cols & op_cols):
                            errors.append(
                                f"Operator_Precedence_Error: '{op.sql(dialect=dialect)}' was part of a "
                                f"parenthesized OR-group in the original but is now AND-ed directly with an "
                                f"unrelated predicate — AND binds tighter than OR, so this changes the result. "
                                f"Wrap the OR-group in explicit parentheses, or return the original UNCHANGED."
                            )
                            break
                    else:
                        continue
                    break

        def _cartesian_joins(ast) -> set[str]:
            """Return alias/name of BASE TABLES joined without a real ON condition.

            Only base-table x base-table joins without ON are flagged as accidental Cartesians
            (the q38/q69-style `JOIN base_table ON TRUE`). An explicit `CROSS JOIN [LATERAL]` to a
            derived table / subquery is intentional and valid (e.g. TPC-DS q9 conditional-aggregation
            consolidation: `CROSS JOIN LATERAL (single-row aggregate)`) — exempt it and let the hash
            (equivalence) and time/cost gates judge a genuinely bad cross join instead.
            """
            tables: set[str] = set()
            for join in ast.find_all(exp.Join):
                if not isinstance(join.this, exp.Table):
                    continue  # subquery / derived table / LATERAL — intentional, not an accidental Cartesian
                kind = join.args.get("kind") or ""
                if isinstance(kind, str) and kind.upper() == "CROSS":
                    tables.add(join.this.alias_or_name.lower())
                    continue
                on = join.args.get("on")
                if on is not None and on.sql().strip().lower() in ("true", "1 = 1", "1=1"):
                    tables.add(join.this.alias_or_name.lower())
            return tables

        orig_cartesian = _cartesian_joins(orig_ast)
        opt_cartesian  = _cartesian_joins(opt_ast)
        for tbl in sorted(opt_cartesian - orig_cartesian):
            errors.append(
                f"Cartesian_Join_Detected: '{tbl}' is joined without an ON condition — "
                f"restore the join condition from the original WHERE clause as the ON condition for this table; "
                f"if you cannot determine the correct ON condition, return the original query UNCHANGED"
            )

        opt_text = opt_ast.sql(dialect=dialect).lower()
        orig_where = orig_ast.find(exp.Where)
        if orig_where:
            for cond in _split_and(orig_where.this):
                refs = {col.table.lower() for col in cond.find_all(exp.Column) if col.table}
                if len(refs) != 1:
                    continue
                alias = list(refs)[0]

                # EXISTS/NOT EXISTS: compare by (inner_tables, corr_col) signature,
                # not by text — internal join syntax changes are semantically neutral.
                if cond.find(exp.Exists):
                    orig_sigs = _exists_signatures(cond, alias)
                    opt_sigs = _exists_signatures(opt_ast, alias)
                    for sig in orig_sigs:
                        if sig not in opt_sigs:
                            tables, corr = sig
                            errors.append(
                                f"Data_Loss_Detected: EXISTS on {set(tables)} "
                                f"(correlated on '{corr}') was removed or altered"
                            )
                    continue

                cond_text = cond.sql(dialect=dialect).lower()
                stripped_text = re.sub(rf'\b{re.escape(alias)}\.', '', cond_text)
                # Constant-fold arithmetic on literals before matching: a rewrite that folds a
                # threshold (e.g. `dyear = 1998 + 1` -> `dyear = 1999`) is equivalent, but a raw
                # text compare misses it and fabricates a Data_Loss. Match the folded form too
                # (purely additive — can only remove false positives, never create a new miss).
                try:
                    from sqlglot.optimizer.simplify import simplify
                    folded = simplify(cond.copy())
                    folded_text = folded.sql(dialect=dialect).lower()
                    folded_stripped = re.sub(rf'\b{re.escape(alias)}\.', '', folded_text)
                except Exception:
                    folded, folded_text, folded_stripped = cond, cond_text, stripped_text
                if (cond_text not in opt_text and stripped_text not in opt_text
                        and folded_text not in opt_text and folded_stripped not in opt_text):
                    # Equality conditions are valid in reversed form (A=B ≡ B=A),
                    # and are often moved from WHERE to JOIN ON clause in rewrites.
                    # Also normalize all table qualifiers — handles the case where the
                    # model adds a new alias to an originally unqualified column
                    # (e.g. mycol → s.mycol after aliasing the table AS s).
                    if isinstance(cond, exp.EQ):
                        # Use the constant-folded EQ so a reversed+folded form (e.g. `1999 = dyear`)
                        # also matches, mirroring the folded direct check above.
                        eq = folded if isinstance(folded, exp.EQ) else cond
                        rev = f"{eq.right.sql(dialect=dialect).lower()} = {eq.left.sql(dialect=dialect).lower()}"
                        rev_stripped = re.sub(rf'\b{re.escape(alias)}\.', '', rev)
                        norm_cond = re.sub(r'\b\w+\.', '', folded_text)
                        norm_rev  = re.sub(r'\b\w+\.', '', rev)
                        norm_opt  = re.sub(r'\b\w+\.', '', opt_text)
                        if (rev in opt_text or rev_stripped in opt_text
                                or norm_cond in norm_opt or norm_rev in norm_opt):
                            continue
                    or_expr = cond.find(exp.Or)
                    if or_expr:
                        def _present(node, _alias=alias, _opt=opt_text, _d=dialect) -> bool:
                            t = node.sql(dialect=_d).lower()
                            s = re.sub(rf'\b{re.escape(_alias)}\.', '', t)
                            return t in _opt or s in _opt
                        if _present(or_expr.left) and _present(or_expr.right):
                            continue
                    errors.append(f"Data_Loss_Detected: filter '{cond_text}' was removed or altered")

        # Numeric-constant guard. A semantically-equivalent rewrite preserves every threshold /
        # multiplier / filter value (e.g. the `* 1.2` in `avg(x) * 1.2`). When the model decorrelates
        # but DROPS the factor (writes `> avg_x` instead of `> avg_x * 1.2`), the result set changes
        # but no table/column/filter is "removed" — the checks above miss it and only S=1 catches it,
        # AFTER paying to execute. Flagging it structurally turns a semantic slip into a MECHANICAL
        # signal the corrector AND every reflection attempt can act on, before execution. Neutral
        # elements (0, 1) are ignored: `x * 1.0 -> x` and `+ 0` are valid simplifications.
        def _numeric_literals(ast) -> set[float]:
            out: set[float] = set()
            for lit in ast.find_all(exp.Literal):
                if lit.is_string:
                    continue
                try:
                    out.add(float(lit.this))
                except (TypeError, ValueError):
                    pass
            return out

        missing_consts = (_numeric_literals(orig_ast) - _numeric_literals(opt_ast)) - {0.0, 1.0}
        new_consts = (_numeric_literals(opt_ast) - _numeric_literals(orig_ast)) - {0.0, 1.0}
        # Only a PURE drop (a constant vanished and NOTHING new replaced it) is a structural red flag — e.g.
        # the model wrote `> avg_x` instead of `> avg_x * 1.2`. When the rewrite introduces a NEW literal, a
        # valid CONSTANT FOLD likely happened (e.g. `1189 + 11` → `1200`, or `x/100*5` → `x*0.05`): NOT a drop
        # — don't pre-reject, let the S=1 hash judge equivalence (it is the authority; this check is a cheap
        # proxy that must not fabricate a failure on legitimate arithmetic folding — cf. q38).
        if missing_consts and not new_consts:
            for c in sorted(missing_consts):
                val = int(c) if c.is_integer() else c
                errors.append(
                    f"Data_Loss_Detected: numeric constant '{val}' from the original is missing in the "
                    f"rewrite — a threshold factor, multiplier, or filter value was dropped or changed. "
                    f"Restore the exact constant from the original (e.g. keep '* {val}' in the comparison)."
                )

        return errors

    def run_analyze(self, tables: list[str]) -> None:
        pass  # no-op — backends without ANALYZE support ignore this call

    @abstractmethod
    def schema_fingerprint(self) -> str | None: ...

    @abstractmethod
    def is_timeout_error(self, error_message: str) -> bool: ...

    def cost_gate_error(self, original_cost: float, optimized_cost: float, timing: dict) -> str | None:
        """Return an error string if the cost increase should reject the rewrite, else None.

        The planner cost is only a PROXY for performance, used to reject rewrites that
        would be slower. But we also measure the REAL execution time via EXPLAIN ANALYZE —
        and when the real time improves, the cost estimate is simply wrong and must not
        veto the rewrite. This is the CTE-paradox case: both PostgreSQL and MySQL lose
        cardinality statistics across a CTE/derived-table boundary and severely overestimate
        the cost of CTE-based rewrites (PostgreSQL's 700x cardinality error on JOB 29c is the
        canonical example), even though the rewrite executes much faster. Real measurement
        wins over a known-unreliable estimate. (See insights.md.)
        """
        # Real time wins over the (unreliable) cost estimate. Honour BOTH the exact improvement AND the
        # timeout LOWER BOUND: on a timed-out original, improvement_pct is None but a positive lower bound
        # still means the rewrite is FASTER. Without this, q1/MySQL (91% faster via lower bound, yet the
        # MySQL planner over-estimates the rewrite's cost ~3000×) was WRONGLY cost-rejected — exactly the
        # cost≠time veto this gate exists to prevent.
        gain = timing.get("improvement_pct")
        if gain is None:
            gain = timing.get("improvement_lower_bound_pct")
        if (gain or 0) > 0:
            return None
        cost_increase_pct = ((optimized_cost - original_cost) / original_cost * 100) if original_cost else 0
        if cost_increase_pct > 1.0:
            return f"Significant cost increase: original={original_cost:.2f} → optimized={optimized_cost:.2f} (+{cost_increase_pct:.1f}%)"
        return None

    def is_float_threshold_comparison(self, sql: str) -> bool:
        """True if the query compares a value against a FLOATING-POINT AGGREGATE threshold
        (`x > AVG(...)`, `x >= SUM(...)*k`, …). Such predicates are SCALE-FRAGILE: float
        summation is non-associative, so the aggregate's last bits depend on the accumulation
        order, which the planner changes between a correlated original and a decorrelated
        rewrite. Rows whose value sits exactly ON the threshold can flip → a real, scale-dependent
        result divergence that a small (SF1) sample cannot exercise (q1 holds at scale; its twin
        q30 does NOT — identical shape, data-dependent). Used only to TAG (never gate) a land whose
        equivalence was established solely on the small instance, so cross-engine / full-scale can
        adjudicate. Dialect-agnostic (AVG/SUM/comparison are standard SQL); heuristic, errs toward
        flagging. See fixed_bugs.md #17."""
        s = re.sub(r"\s+", " ", (sql or "")).lower()
        # a comparison operator governing an AVG/SUM (optionally through a scalar subquery)
        if re.search(r"[<>]=?\s*\(?\s*(select\b[^()]*?)?\b(avg|sum)\s*\(", s):
            return True
        # an AVG/SUM scaled by a factor (the `avg(...)*1.2` threshold form) anywhere a comparison exists
        if re.search(r"\b(avg|sum)\s*\([^()]*\)\s*\*\s*[0-9.]", s) and re.search(r"[<>]=?", s):
            return True
        return False

    def _table_columns(self, table: str) -> set[str]:
        """Lowercased column names of `table`, or empty set if unknown. Concrete backends
        override with an information_schema lookup. Empty set = 'can't tell' → callers fail open."""
        return set()

    # Concrete sqlglot node types that produce a boolean — a valid WHERE/partial-index predicate
    # must be (or be wrapped around) one of these. We list concrete types on purpose: exp.Column
    # subclasses exp.Condition, so the broad base classes would wrongly accept a bare column.
    _BOOLEAN_PREDICATE_TYPES: tuple = (
        exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
        exp.And, exp.Or, exp.Not, exp.Is, exp.In, exp.Like, exp.ILike, exp.Between,
    )

    def _sanitize_partial_predicate(self, table: str, predicate: str | None) -> str | None:
        """Drop a partial-index WHERE predicate that references columns not on the indexed table.

        The LLM advisor sometimes lifts a query's WHERE filters (a filter on a column that
        comes from a JOINED table) and attaches them as a partial-index predicate on a
        *different* table. A partial-index predicate may only reference the indexed
        table's own columns, so such DDL is invalid (the engine raises UndefinedColumn).
        Here we detect it and degrade to a full index instead. Fail-open: if we can't
        determine the table's columns or parse the predicate, we leave it unchanged.
        """
        if not predicate:
            return predicate
        cols = self._table_columns(table)
        if not cols:
            return predicate  # unknown columns — don't second-guess
        try:
            pred_ast = sqlglot.parse_one(predicate, dialect=self.sqlglot_dialect())
        except Exception:
            return predicate
        # A partial-index predicate must be a boolean condition. Models sometimes return just the
        # indexed column(s) — "(d_date_sk)" or "(c1, c2)" — which Postgres rejects (WHERE wants a
        # boolean, not an integer/record). Unwrap parens and require a comparison/logical node;
        # otherwise drop the predicate and degrade to a full index. (deepseek-r1 q1, 2026-06-15)
        node = pred_ast
        while isinstance(node, exp.Paren):
            node = node.this
        if not isinstance(node, self._BOOLEAN_PREDICATE_TYPES):
            return None
        referenced = {c.name.lower() for c in pred_ast.find_all(exp.Column)}
        if referenced and not (referenced <= cols):
            return None  # references foreign columns → drop the predicate (full index)
        return predicate

    # Postgres access methods. Models spell btree as "b-tree"/"b tree"/"BTREE" etc.; normalize to
    # the canonical token so it is valid in `USING <am>` and in the generated index name. Anything
    # we don't recognize falls back to btree (the safe default for the column types we index).
    _ACCESS_METHODS: frozenset[str] = frozenset({"btree", "gin", "gist", "spgist", "brin", "hash"})

    def _normalize_index_type(self, raw: str | None) -> str:
        if not raw:
            return "btree"
        token = re.sub(r"[-_ ]", "", raw.strip().lower())
        return token if token in self._ACCESS_METHODS else "btree"

    def generate_index_ddl(self, spec: dict) -> str:
        table = spec["target"]
        # LLM occasionally wraps composite column lists in parentheses: "(col1, col2)" → strip
        column = spec["property"].strip("() ")
        index_type = self._normalize_index_type(spec.get("index_type"))
        partial = self._sanitize_partial_predicate(table, spec.get("partial_predicate"))
        idx_name = f"idx_{table}_{column}_{index_type}"
        using = f" USING {index_type}" if index_type != "btree" else ""
        where = f" WHERE {partial}" if partial else ""
        return f"CREATE INDEX {idx_name} ON {table}{using} ({column}){where}"

    def simulate_index(self, spec: dict, query: str, workload: list[dict] | None = None) -> dict:
        return {
            "table": spec.get("target", "unknown"),
            "column": spec.get("property", "unknown"),
            "index_type": spec.get("index_type", "btree"),
            "ddl": spec.get("ddl", ""),
            "estimated_benefit": spec.get("estimated_benefit", ""),
            "original_plan_cost": None,
            "optimized_plan_cost": None,
            "estimated_read_benefit_pct": None,
            "write_overhead_pct": None,
            "recommendation": "index simulation not supported for this backend",
        }

    def existing_index_covering(self, table: str, columns: list[str], index_type: str) -> str | None:
        """Catalog lookup for an existing index already covering these columns.
        Override per backend; default is unknown — never skip a candidate."""
        return None

    # --- Workload-aware simulation (shared by all SQL backends) ---

    def _workload_baselines(self, workload: list[dict] | None) -> list[dict]:
        """Plan cost of each workload query WITHOUT the candidate index (current bank state)."""
        baselines = []
        for entry in workload or []:
            sql = entry.get("sql")
            if not sql:
                continue
            cost = self.get_total_cost(self.explain(sql))
            baselines.append({"source": entry.get("source", "?"), "sql": sql, "baseline_cost": cost})
        return baselines

    @staticmethod
    def _build_workload_impact(baselines: list[dict], costs_with_index: dict) -> dict | None:
        """Aggregate per-query impact of the candidate index on the rest of the workload."""
        if not baselines:
            return None
        queries = []
        improved = regressed = unaffected = 0
        for b in baselines:
            with_idx = costs_with_index.get(b["source"])
            benefit = None
            if b["baseline_cost"] and with_idx is not None:
                benefit = round(((b["baseline_cost"] - with_idx) / b["baseline_cost"]) * 100, 1)
                if benefit > 0.5:
                    improved += 1
                elif benefit < -0.5:
                    regressed += 1
                else:
                    unaffected += 1
            queries.append({
                "source": b["source"],
                "baseline_cost": b["baseline_cost"],
                "cost_with_index": with_idx,
                "benefit_pct": benefit,
            })
        measured = [q["benefit_pct"] for q in queries if q["benefit_pct"] is not None]
        return {
            "queries": queries,
            "queries_improved": improved,
            "queries_regressed": regressed,
            "queries_unaffected": unaffected,
            "aggregate_benefit_pct": round(sum(measured) / len(measured), 1) if measured else None,
        }

    @staticmethod
    def _append_workload_note(rec: str, workload_impact: dict | None) -> str:
        if not workload_impact:
            return rec
        if workload_impact["queries_regressed"] > 0:
            return rec + (
                f" ⚠ WORKLOAD: regresses {workload_impact['queries_regressed']} other "
                f"workload quer(ies) — review workload_impact before creating"
            )
        if workload_impact["queries_improved"] > 0:
            return rec + (
                f" [workload: also improves {workload_impact['queries_improved']} other "
                f"quer(ies), aggregate {workload_impact['aggregate_benefit_pct']}%]"
            )
        return rec + " [workload: no effect on other workload queries]"

    def _format_schema_for_index_prompt(self, schema_context: dict, query_tables: set[str]) -> str:
        relevant = [t for t in schema_context.get("tables", []) if t["name"].lower() in query_tables]

        # POSITION-BIAS PROBE (2026-08-25). `SCHEMA_ORDER=reverse` flips the table order in this block
        # and changes NOTHING else — same tables, same columns, same row counts, same indexes.
        #   WHY IT EXISTS: LLM4IA (CIKM '25) demonstrate that GPT-4o "places higher attention on the
        # beginning and ending parts of the workload, ignoring relevant information in the middle",
        # and pick the wrong index because of it. This prompt hands the model a schema block and asks
        # it to choose columns from it — exactly that configuration. If our advisor is position-biased,
        # part of what we report as a FIND limitation is an artifact of ordering.
        #   READING: run the same queries under both orders. Same recommendations => capacity.
        # Different recommendations => positional bias, and we declare it with a measured magnitude
        # instead of as an unquantified threat.
        #   ⛔ DEFAULT IS UNCHANGED ORDER. This is a probe lever, never a campaign setting: flipping it
        # mid-campaign would make two halves of a cell incomparable.
        if os.getenv("SCHEMA_ORDER", "").strip().lower() == "reverse":
            relevant = list(reversed(relevant))

        lines = []
        for t in relevant:
            lines.append(f"\nTable: {t['name']} ({t['row_count']:,} rows)")
            for idx in t.get("indexes", []):
                kind = "UNIQUE INDEX" if idx["unique"] else "INDEX"
                predicate = f" WHERE {idx['predicate']}" if idx.get("predicate") else ""
                lines.append(f"  {kind}: {idx['name']} on ({', '.join(str(c) for c in idx['columns'])}){predicate}")
            if not t.get("indexes"):
                lines.append("  (no indexes)")
        return "".join(lines)

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
        # WORKLOAD-AWARE generation (the second form): when present, the model sees the OTHER queries that
        # share these tables and is told to favor index columns that speed up MANY of them, not just this one.
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
1. Find full table scans on large tables — these are the primary index candidates.
   Also look inside SubPlan and InitPlan nodes in the execution plan — EXISTS/NOT EXISTS
   subqueries execute as correlated subplans and their scans are often the real bottleneck.
2. For EXISTS/NOT EXISTS in the SQL, identify the join condition columns inside them
   (e.g. WHERE outer.id = inner.fk) — those inner columns are strong index candidates.
3. Identify the column causing the scan: the WHERE filter or JOIN condition on that table.
4. Decide the appropriate index type for that column and access pattern.
5. If multiple scans exist, order the indexes by the number of rows scanned — largest first.
   The index that eliminates the scan on the most rows is always the highest priority,
   regardless of index type or whether simulation is possible.
6. Use the OPTIMIZER HINTS section (if present) as additional signal — they may name columns
   that the execution plan alone does not make obvious.
7. Do NOT suggest indexes that already exist on the table.
8. If there is no clear full-table-scan bottleneck, set no_index_possible to true.

Return ONLY this JSON structure — no explanation outside the JSON:
{{
  "indexes": [
    {{
      "target": "<table_name>",
      "property": "<column_name>",
      "index_type": "<index type>",
      "partial_predicate": null,
      "estimated_benefit": "<what scan this eliminates and on how many rows>"
    }}
  ],
  "no_index_possible": false,
  "explanation": "<why these indexes were chosen, or why no index is possible>"
}}"""

    def implicit_join_hint(self, raw_sql: str, error_message: str) -> str | None:
        """Inject a hint when UndefinedColumn occurs because an implicit-join table's column was placed
        in the ON clause of a different table before that table was joined."""
        if "UndefinedColumn" not in error_message and "undefined column" not in error_message.lower():
            return None

        col_match = re.search(r'column "([^"]+)" does not exist', error_message, re.IGNORECASE)
        if not col_match:
            return None
        undefined_col = col_match.group(1)

        try:
            parsed = sqlglot.parse_one(raw_sql, dialect=self.sqlglot_dialect())
        except Exception:
            return None

        # Don't fire for computed/window-function OUTPUT aliases — they are not implicit-join
        # columns. E.g. `max(...) over (...) AS running_total` then used in an outer WHERE: the
        # UndefinedColumn there means the alias wasn't projected at that level, not an implicit join.
        is_output_alias = any(
            a.alias and a.alias.lower() == undefined_col.lower()
            for a in parsed.find_all(exp.Alias)
        )
        if is_output_alias:
            return None

        where = parsed.find(exp.Where)
        if not where:
            return None

        # Check column appears unqualified in original WHERE (sign of implicit join)
        unqualified = any(
            col.name.lower() == undefined_col.lower() and not col.table
            for col in where.find_all(exp.Column)
        )
        if not unqualified:
            return None

        # Exclude tables inside EXISTS/NOT EXISTS/IN subqueries — they are not implicit-join tables
        subquery_table_ids: set[int] = set()
        for node in list(parsed.find_all(exp.Exists)) + list(parsed.find_all(exp.Subquery)):
            for t in node.find_all(exp.Table):
                subquery_table_ids.add(id(t))

        unaliased = list(dict.fromkeys(
            t.name for t in parsed.find_all(exp.Table)
            if not t.alias and id(t) not in subquery_table_ids
        ))

        # Extract the correlated condition: undefined_col = <other_col> from original WHERE
        corr_col: str | None = None
        for col_node in where.find_all(exp.Column):
            if col_node.name.lower() == undefined_col.lower() and not col_node.table:
                parent = col_node.parent
                if parent and isinstance(parent, exp.EQ):
                    other = parent.right if parent.left is col_node else parent.left
                    if hasattr(other, "sql"):
                        corr_col = other.sql(dialect=self.sqlglot_dialect())
                break

        owner_tbl = unaliased[0] if unaliased else "<table>"

        hint = (
            f"Hint: '{undefined_col}' belongs to table '{owner_tbl}' — in the original it is joined via implicit "
            f"syntax (FROM ..., {owner_tbl} ... WHERE {undefined_col} = ...). In your rewrite it was referenced "
            f"before '{owner_tbl}' was joined (e.g. placed in another table's ON clause). "
            f"To fix: give '{owner_tbl}' its own explicit JOIN and put the '{undefined_col} = ...' condition in "
            f"that JOIN's ON clause."
        )
        if corr_col:
            hint += f" The correct form is: JOIN {owner_tbl} ON {undefined_col} = {corr_col}."

        return hint

    def window_filter_hint(self, optimized_query: str, error_message: str) -> str | None:
        """Hint when UndefinedColumn names a window-function OUTPUT alias referenced in WHERE at the
        same query level. Window functions are computed AFTER WHERE, so their aliases aren't
        available there — the SELECT must be wrapped in a subquery and the filter applied outside.
        General SQL rule (no benchmark- or query-specific knowledge)."""
        if "UndefinedColumn" not in error_message and "undefined column" not in error_message.lower():
            return None
        col_match = re.search(r'column "([^"]+)" does not exist', error_message, re.IGNORECASE)
        if not col_match:
            return None
        undefined_col = col_match.group(1)
        try:
            parsed = sqlglot.parse_one(optimized_query, dialect=self.sqlglot_dialect())
        except Exception:
            return None
        # Is undefined_col defined as an alias over a window function anywhere in the query?
        is_window_alias = any(
            a.alias and a.alias.lower() == undefined_col.lower() and a.find(exp.Window)
            for a in parsed.find_all(exp.Alias)
        )
        if not is_window_alias:
            return None
        return (
            f"Hint: '{undefined_col}' is the output of a window function (OVER ...). Window functions are "
            f"computed AFTER the WHERE clause, so '{undefined_col}' cannot be referenced in WHERE at the same "
            f"query level. To filter on it, wrap that SELECT in a subquery and apply the '{undefined_col}' "
            f"condition in the outer query."
        )

    def mechanical_write_hint(self, optimized_query: str, error_message: str = "") -> str | None:
        """Generic SQL-CORRECTNESS diagnostic for a rewrite the model ALREADY chose — purely MECHANICAL:
        no benchmark identifier (RQ2), no optimization technique named (RQ1), true for any relational DB.
        Reactive to the model's own error — it helps the model write VALID SQL for the technique it picked,
        never WHICH technique to pick (so the FIND stays unaided; only the WRITE is scaffolded). Covers the
        recurring WRITE slips seen on q9: clause order, double WHERE, per-group average."""
        sql_l = (optimized_query or "").lower()
        err_l = (error_message or "").lower()
        # Clause order — a JOIN/CROSS JOIN/LATERAL placed AFTER the WHERE is invalid; joins precede WHERE.
        if "syntax error" in err_l and re.search(r"near .{0,4}(cross|lateral|join)\b", err_l):
            return (
                "Clause order: a JOIN / CROSS JOIN / LATERAL placed AFTER the WHERE clause is invalid SQL — "
                "every join must come BEFORE the WHERE (FROM ... JOIN ... WHERE ...). Or put the precomputed "
                "aggregates as a derived table in the FROM list: FROM (SELECT ... FROM t) AS d, other WHERE ..."
            )
        # Double WHERE at one level — fire ONLY on an actual SYNTAX ERROR at a WHERE token. A legit
        # subquery WHERE must NOT trigger this, and it must NOT fire on execution/timeout errors whose
        # message merely echoes the SQL (which contains "where"). PG reports a stray WHERE as
        # "syntax error at or near \"where\"".
        if "syntax error" in err_l and re.search(r'near\s+"?where', err_l):
            return "A query level has exactly ONE WHERE clause — merge conditions with AND, not a second WHERE."
        # Per-group average — dividing a conditional SUM by count(*) uses the TOTAL count, not the group's.
        if (("hashes differ" in err_l) or ("integrity" in err_l) or ("s≠1" in err_l)) and "case when" in sql_l \
                and re.search(r"/\s*count\s*\(\s*\*\s*\)", sql_l):
            return (
                "A per-group average is avg(col) restricted to the group, NOT sum(col)/count(*): count(*) is "
                "the TOTAL row count, not the group's, so that division is wrong. Use "
                "avg(CASE WHEN <cond> THEN col END) (NULLs are skipped) for the group's average."
            )
        return None

    def has_correlated_subquery(self, sql: str) -> bool:
        """True if the query contains a correlated subquery — an inner SELECT that references a
        column from an enclosing query (e.g. `> (SELECT avg(x) FROM t2 WHERE t2.k = outer.k)`).
        Engine-agnostic SQL scope analysis (sqlglot); no benchmark/query-specific knowledge.
        Used to fact-check a "decorrelate" technique label against what the SQL actually does."""
        try:
            from sqlglot.optimizer.scope import build_scope
            ast = sqlglot.parse_one(sql, dialect=self.sqlglot_dialect())
            root = build_scope(ast)
            if root is None:
                return False
            return any(getattr(s, "is_correlated_subquery", False) for s in root.traverse())
        except Exception:
            return False

    def verify_technique_labels(self, raw_sql: str, optimized_sql: str, labels: list[str]) -> list[str]:
        """Deterministic fact-check on the post-hoc technique labels (Direction B->A discovery feed).

        The labeler is an LLM — often the same small model under test — and can hallucinate a
        technique the rewrite did NOT apply: e.g. naming "decorrelate scalar subquery ..." while the
        correlated subquery is STILL present (observed: deepseek-llama q1, kept the correlated
        subquery 3/3 yet got correlation-handling labels). A false label inflates cross-model
        convergence in the discovery store and corrupts the heuristic ranking. This drops ANY label
        that claims to address correlation (match on `correlat` — catches "decorrelate", "correlated
        subquery handling", "convert correlated subquery ...") whenever the optimized SQL STILL
        contains a correlated subquery: if the correlation persists, no such claim is supported. The
        substring is broad on purpose — a stochastic labeler phrases the same false claim many ways.

        Observability-only and purely deflationary: it can only REMOVE an unsupported label, never
        add one — so it cannot bias results toward the thesis (it makes a weak model look correctly
        weaker, not stronger). When the rewrite genuinely decorrelates (no correlation left) the guard
        does not run, so a legitimate "replace correlated subquery with join" label survives.
        Fail-open: returns the labels unchanged on any parse error."""
        if not labels:
            return labels
        # (1) Decorrelation CLAIMED but the correlation PERSISTS in the optimized → drop any correlation-
        # handling claim (broad `correlat`: "decorrelate", "convert correlated subquery", …). llama q1.
        try:
            if self.has_correlated_subquery(optimized_sql):
                return [l for l in labels if "correlat" not in l.lower()]
        except Exception:
            return labels
        # (2) The ORIGINAL has NO correlated scalar-aggregate subquery → a "decorrelate" claim is
        # IMPOSSIBLE (you cannot decorrelate what has no correlation). Observed: TPC-DS q9 — INDEPENDENT
        # repeated scalar-aggregate subqueries that the model CONSOLIDATES, mis-labeled "decorrelate scalar
        # aggregate subquery into CTE plus join" → would pollute the (A-seeded) decorrelation cluster + get
        # the whole cluster skipped. Uses the PRECISE recognizer (`detect_correlated_scalar_aggregate`, what
        # A is gated on) — NOT the loose `has_correlated_subquery`, which false-positives on q9. Matches the
        # VERB `decorrelat` only (not the noun `correlat`), so a legitimate "consolidate multiple [correlated]
        # subqueries into one" label survives. Deflationary: removes a false label, never adds one.
        try:
            from src.pipeline.backends.relational.sql_rewriter import detect_correlated_scalar_aggregate
            if detect_correlated_scalar_aggregate(raw_sql, self.sqlglot_dialect()) is None:
                return [l for l in labels if "decorrelat" not in l.lower()]
        except Exception:
            return labels
        return labels

    def incomplete_technique_hint(self, raw_query: str, optimized_query: str) -> str | None:
        """Reflection hint for the REASONING model (architect) — NEVER the corrector. When the ORIGINAL
        had a correlated scalar subquery and the rewrite STILL has one, the decorrelation was only
        half-applied (the aggregate may be precomputed, but it is still read once per outer row). The
        caller gates this on APPLICATION mode. Converting correlated→JOIN IS the optimization itself, so
        this guidance must drive the reasoning model's next attempt — the code model must not perform it
        (that would attribute the optimization to the coder, not the small reasoning model). Returns a
        non-mechanical hint string (so is_mechanical_failure stays False and the corrector never fires)."""
        try:
            if self.has_correlated_subquery(raw_query) and self.has_correlated_subquery(optimized_query):
                return (
                    "Decorrelation incomplete: the rewrite STILL contains a correlated scalar subquery "
                    "(a subquery that references the outer row's column). Precompute the aggregate GROUPED "
                    "BY the correlation key in a CTE/derived table and JOIN it on that key — so it is "
                    "evaluated once, not once per outer row. Do not keep the correlated subquery form."
                )
        except Exception:
            return None
        return None

    def causal_regression_hint(self, optimized_query: str) -> str | None:
        try:
            parsed = sqlglot.parse_one(optimized_query, dialect=self.sqlglot_dialect())
        except Exception:
            return None

        # Hint 1: CTE with non-sargable LIKE (actionable — tell model to remove CTE)
        for cte in parsed.find_all(exp.CTE):
            for like in cte.find_all(exp.Like):
                pattern_node = like.args.get("expression")
                if pattern_node is None:
                    continue
                pattern_str = pattern_node.name or str(pattern_node)
                if pattern_str.startswith("%"):
                    table_alias = cte.alias or "unknown"
                    return (
                        f"Hint: the CTE on '{table_alias}' materialized rows using a LIKE filter with "
                        f"a leading or trailing '%' — this pattern is non-sargable (no B-tree index covers it). "
                        f"The full table scan happens inside the CTE regardless of join order. "
                        f"Instead of pre-filtering with a CTE, arrive at '{table_alias}' late in the explicit "
                        f"JOIN chain and apply the LIKE condition in the ON clause — the join itself may "
                        f"filter more rows before reaching this table."
                    )

        # Hint 2: non-sargable LIKE outside any CTE (terminal — no SQL rewrite can fix this)
        cte_like_ids: set[int] = set()
        for cte in parsed.find_all(exp.CTE):
            for like in cte.find_all(exp.Like):
                cte_like_ids.add(id(like))

        for like in parsed.find_all(exp.Like):
            if id(like) in cte_like_ids:
                continue
            pattern_node = like.args.get("expression")
            if pattern_node is None:
                continue
            pattern_str = pattern_node.name or str(pattern_node)
            if pattern_str.startswith("%"):
                col = like.args.get("this")
                col_str = str(col) if col else "the filtered column"
                return (
                    f"Hint: '{col_str} LIKE '{pattern_str}'' is non-sargable even without a CTE — "
                    f"no B-tree index covers a leading '%' pattern. "
                    f"The Seq Scan on this table is unavoidable with any SQL rewrite. "
                    f"The correct response is no_change: true. "
                    f"{self._nonsargable_remedy()} is required for this query to be performant — "
                    f"that is a DDL operation outside the current optimization scope."
                )

        return None
