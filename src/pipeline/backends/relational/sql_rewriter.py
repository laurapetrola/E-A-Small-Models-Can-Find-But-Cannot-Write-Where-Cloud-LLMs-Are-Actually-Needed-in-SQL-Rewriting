import sqlglot
import sqlglot.expressions as exp
from collections import defaultdict


def _split_and(expr) -> list:
    if isinstance(expr, exp.And):
        return _split_and(expr.left) + _split_and(expr.right)
    return [expr]


def _referenced_aliases(expr) -> set[str]:
    return {col.table.lower() for col in expr.find_all(exp.Column) if col.table}


def parse_components(sql: str, dialect: str = "postgres") -> dict | None:
    try:
        ast = sqlglot.parse_one(sql, dialect=dialect)
    except Exception:
        return None

    # Queries with CTEs mix outer and inner tables — decomposed path can't handle them correctly.
    # Route to fallback prompt which preserves the CTE definition as-is.
    if ast.find(exp.With):
        return None

    # Set operations (INTERSECT/UNION/EXCEPT) and EXISTS/NOT EXISTS subqueries cannot be flattened
    # into a single outer table list: the decomposed parser would lift the branch/subquery tables
    # into the outer FROM with no join predicate connecting them → reconstruct emits `ON TRUE`
    # (Cartesian). This was the real cause of the "Cartesian" on TPC-DS q38 (INTERSECT) and q69
    # (EXISTS/NOT EXISTS) — a parser bug, not a model limit. Route them to the free-form prompt,
    # which preserves these structures intact (and is told to leave EXISTS unchanged).
    if ast.find(exp.Union, exp.Intersect, exp.Except):
        return None
    if ast.find(exp.Exists):
        return None

    # Identify Table nodes that live inside IN subqueries — exclude from outer tables
    subquery_node_ids: set[int] = set()
    for in_expr in ast.find_all(exp.In):
        subq = in_expr.args.get("query")
        if isinstance(subq, exp.Subquery):
            for t in subq.find_all(exp.Table):
                subquery_node_ids.add(id(t))

    # Outer tables only (not those inside IN subqueries)
    tables: dict[str, str] = {}
    for t in ast.find_all(exp.Table):
        if id(t) not in subquery_node_ids:
            alias = (t.alias or t.name).lower()
            tables[alias] = t.name.lower()

    # Detect IN (subquery) conditions in WHERE before classifying other conditions
    in_subqueries: list[str] = []
    join_preds: dict[tuple, list[str]] = defaultdict(list)
    filters: dict[str, list[str]] = defaultdict(list)

    where = ast.find(exp.Where)
    if where:
        for cond in _split_and(where.this):
            if isinstance(cond, exp.In) and isinstance(cond.args.get("query"), exp.Subquery):
                in_subqueries.append(cond.sql(dialect=dialect))
                continue
            refs = _referenced_aliases(cond)
            if len(refs) == 2:
                key = tuple(sorted(refs))
                join_preds[key].append(cond.sql(dialect=dialect))
            elif len(refs) == 1:
                filters[list(refs)[0]].append(cond.sql(dialect=dialect))

    # Also extract join predicates from explicit JOIN ON clauses (e.g. after normalize_implicit_joins)
    for join in ast.find_all(exp.Join):
        on = join.args.get("on")
        if on:
            for cond in _split_and(on):
                refs = _referenced_aliases(cond)
                if len(refs) == 2:
                    key = tuple(sorted(refs))
                    cond_sql = cond.sql(dialect=dialect)
                    if cond_sql not in join_preds.get(key, []):
                        join_preds[key].append(cond_sql)

    # Need at least 2 outer tables OR at least one IN subquery to have something to optimize
    if len(tables) < 2 and not in_subqueries:
        return None

    # Connectivity guard: if any outer table has NO join predicate linking it to another, the
    # reconstruction would emit `JOIN ... ON TRUE` for it (Cartesian). This happens when the real
    # join conditions live inside nested derived tables / scalar subqueries that we don't flatten —
    # the top-level WHERE has no join preds (e.g. TPC-DS q67 rollup-in-derived-table, q9 scalar
    # subqueries in SELECT). The decomposed path can't handle these; route to free-form, which
    # preserves the original structure intact.
    connected_aliases = {alias for key in join_preds for alias in key}
    if len(tables) >= 2 and any(t not in connected_aliases for t in tables):
        return None

    select_node = ast.find(exp.Select)
    select_exprs = [s.sql(dialect=dialect) for s in select_node.expressions] if select_node else ["*"]

    group_by = ast.find(exp.Group)
    order_by = ast.find(exp.Order)
    having = ast.find(exp.Having)

    return {
        "tables": tables,
        "join_predicates": dict(join_preds),
        "filters": dict(filters),
        "in_subqueries": in_subqueries,
        "select": select_exprs,
        "group_by": group_by.sql(dialect=dialect) if group_by else None,
        "order_by": order_by.sql(dialect=dialect) if order_by else None,
        "having": having.sql(dialect=dialect) if having else None,
    }


def detect_correlated_scalar_aggregate(sql: str, dialect: str = "postgres") -> dict | None:
    """Recognize the DECORRELATION shape (recognition only — no transform):

        outer_col OP (SELECT agg(...) FROM T alias WHERE outer.key = alias.key [AND ...])

    a single-table correlated scalar aggregate subquery in a comparison. Returns the parts the
    decorrelation transform needs (the value column, the aggregate projection, the inner table and
    the correlation key on both sides) or None if the query has no such shape. This is the parser-side
    plumbing that lets the decomposed path REACH the (loop-generated) decorrelation transform for a
    shape that parse_components otherwise bails on (CTE / scalar subquery)."""
    try:
        ast = sqlglot.parse_one(sql, dialect=dialect)
    except Exception:
        return None

    for cmp in ast.find_all(exp.GT, exp.LT, exp.GTE, exp.LTE, exp.EQ, exp.NEQ):
        for outer_operand, sub in ((cmp.this, cmp.expression), (cmp.expression, cmp.this)):
            if not isinstance(sub, exp.Subquery) or not isinstance(outer_operand, exp.Column):
                continue
            inner = sub.this
            if not isinstance(inner, exp.Select) or len(inner.expressions) != 1:
                continue
            if inner.expressions[0].find(exp.AggFunc) is None:
                continue
            inner_tables = {(t.alias or t.name).lower(): t.name.lower() for t in inner.find_all(exp.Table)}
            if len(inner_tables) != 1:  # single-table aggregate subquery — the decorrelation case
                continue
            inner_alias, inner_table = next(iter(inner_tables.items()))
            where = inner.find(exp.Where)
            if where is None:
                continue
            for eq in where.find_all(exp.EQ):
                cols = [c for c in eq.find_all(exp.Column) if c.table]
                tabs = {c.table.lower() for c in cols}
                if len(cols) == 2 and inner_alias in tabs and len(tabs - {inner_alias}) == 1:
                    outer_alias = next(iter(tabs - {inner_alias}))
                    return {
                        "operator": type(cmp).__name__,
                        "outer_col": outer_operand.sql(dialect=dialect),
                        "agg_select": inner.expressions[0].sql(dialect=dialect),
                        "inner_table": inner_table,
                        "inner_alias": inner_alias,
                        "outer_key": next(c.sql(dialect=dialect) for c in cols if c.table.lower() == outer_alias),
                        "inner_key": next(c.sql(dialect=dialect) for c in cols if c.table.lower() == inner_alias),
                    }
    return None


def parse_decorrelation_components(sql: str, dialect: str = "postgres") -> dict | None:
    """Plumbing/substrate for the DECORRELATION shape — the separate path that does NOT touch the
    live parse_components (which keeps routing this shape to free-form until the loop-generated
    transform ships). It does not build the rewrite; it only EXPOSES the components a decorrelation
    transform needs, for a query parse_components bails on (CTE + correlated scalar subquery):

      - decorrelation: the recognizer descriptor (operator, outer_col, agg_select, inner_table,
                       inner_alias, outer_key, inner_key) — the recognized parts of the subquery
      - ctes:          the existing CTE definitions, PRESERVED as-is ({name, sql}) so the transform
                       can keep them and append its pre-computation CTE alongside
      - ast / sql / dialect: the in-process AST handle and source so the transform can splice the
                       rest of the query deterministically (replace the subquery operand, add the join)

    Returns None when the query has no correlated scalar-aggregate subquery. The decorrelation
    transform (Direction B->A, loop-generated) is grown on top of what this exposes; a reference
    transform in the tests confirms the exposure is sufficient and serves as the equivalence oracle."""
    try:
        ast = sqlglot.parse_one(sql, dialect=dialect)
    except Exception:
        return None

    desc = detect_correlated_scalar_aggregate(sql, dialect=dialect)
    if desc is None:
        return None

    ctes: list[dict] = []
    with_node = ast.find(exp.With)
    if with_node:
        for cte in with_node.expressions:
            ctes.append({"name": cte.alias.lower(), "sql": cte.this.sql(dialect=dialect)})

    return {
        "decorrelation": desc,
        "ctes": ctes,
        "ast": ast,
        "sql": sql,
        "dialect": dialect,
    }


CONSOLIDATION_MIN_REPEATED = 3  # need >= N repeated scalar-aggregate subqueries over one table to consolidate


def _uncorrelated_scalar_agg(sub: "exp.Subquery", dialect: str) -> dict | None:
    """Is this Subquery an UNCORRELATED single-table scalar aggregate — `(SELECT agg(...) FROM T WHERE p)`
    with no outer reference, no GROUP/HAVING/DISTINCT/LIMIT? Returns {agg, pred, table} or None. This is
    the per-bucket recognition for the consolidation shape (purely structural — no benchmark literals)."""
    inner = sub.this
    if not isinstance(inner, exp.Select) or len(inner.expressions) != 1:
        return None
    if inner.find(exp.Group) is not None or inner.args.get("distinct") or inner.args.get("limit"):
        return None
    proj = inner.expressions[0]
    agg = proj.this if isinstance(proj, exp.Alias) else proj
    if not isinstance(agg, exp.AggFunc):
        return None
    tabs = {(t.alias or t.name).lower(): t.name.lower() for t in inner.find_all(exp.Table)}
    if len(tabs) != 1:
        return None
    inner_alias, table = next(iter(tabs.items()))
    where = inner.find(exp.Where)
    if where is None:
        return None
    # Uncorrelated: every qualified column in the WHERE must reference the inner table/alias. A column
    # qualified by an outer alias means a correlated subquery (the decorrelation shape) — not this one.
    for col in where.find_all(exp.Column):
        if col.table and col.table.lower() not in (inner_alias, table):
            return None
    return {"agg": agg, "pred": where.this, "table": table}


def detect_repeated_scalar_aggregates(sql: str, dialect: str = "postgres") -> dict | None:
    """Recognize the CONSOLIDATION shape: >= CONSOLIDATION_MIN_REPEATED uncorrelated scalar-aggregate
    subqueries over the SAME single table (e.g. TPC-DS q9: 15 `(SELECT agg(...) FROM store_sales WHERE
    ss_quantity BETWEEN ...)` repeated). Returns {table, buckets:[{node, agg, pred, alias}], ast} — the
    buckets reference nodes in the RETURNED ast (so the builder mutates that same tree) — or None. Aliases
    are deduped on (agg, pred) so byte-identical subqueries share one precomputed column. Recognition is
    structural (AST shape), never table/column literals → it generalizes to any query of this shape."""
    try:
        ast = sqlglot.parse_one(sql, dialect=dialect)
    except Exception:
        return None
    if ast.find(exp.Select) is None:
        return None

    by_table: dict[str, list] = defaultdict(list)
    for sub in ast.find_all(exp.Subquery):
        info = _uncorrelated_scalar_agg(sub, dialect)
        if info is not None:
            by_table[info["table"]].append((sub, info["agg"], info["pred"]))

    qualifying = [t for t, b in by_table.items() if len(b) >= CONSOLIDATION_MIN_REPEATED]
    if len(qualifying) != 1:
        return None
    table = qualifying[0]
    # Mixed tables (another table also has scalar-agg subqueries) → bail; consolidating only some is unsafe.
    if any(t != table and b for t, b in by_table.items()):
        return None

    alias_of: dict[tuple, str] = {}
    buckets: list[dict] = []
    for sub, agg, pred in by_table[table]:
        key = (agg.sql(dialect=dialect), pred.sql(dialect=dialect))
        if key not in alias_of:
            alias_of[key] = f"_b{len(alias_of) + 1}"
        buckets.append({"node": sub, "agg": agg, "pred": pred, "alias": alias_of[key]})
    return {"table": table, "buckets": buckets, "ast": ast}


def parse_consolidation_components(sql: str, dialect: str = "postgres") -> dict | None:
    """Plumbing/substrate for the CONSOLIDATION shape — exposes the components the consolidation transform
    needs for a query parse_components bails on (scalar subqueries in SELECT). Mirrors
    parse_decorrelation_components; the bucket nodes live in the returned `ast` so the builder mutates that
    tree in place. Returns None when the repeated-scalar-aggregate shape is absent."""
    desc = detect_repeated_scalar_aggregates(sql, dialect=dialect)
    if desc is None:
        return None
    return {
        "consolidation": {"table": desc["table"], "buckets": desc["buckets"]},
        "ast": desc["ast"],
        "sql": sql,
        "dialect": dialect,
    }


_DECORR_CTE = "_decorr"  # alias for the precomputation CTE the decorrelation transform introduces


def _reconstruct_decorrelation(components: dict) -> str | None:
    """DETERMINISTIC decorrelation transform (architecture A — HAND-SEEDED for the high-frequency
    correlated-scalar-aggregate pattern; the auto-formalizer could not synthesize it reliably in 6
    attempts, see experiments_details §5). Promoted from the proven equivalence oracle
    (tests/test_decorrelation_transform.py::_reference_decorrelate).

        outer_col OP (SELECT agg(...) FROM T alias WHERE outer.k = alias.k)
        ->  WITH _decorr AS (SELECT alias.k AS _k, agg(...) AS _v FROM T alias GROUP BY alias.k)
            ... FROM ... JOIN _decorr ON outer.k = _decorr._k ... WHERE outer_col OP _decorr._v

    The INNER join matches the correlated subquery's semantics: a key with no inner rows yields NULL ->
    the comparison is unknown -> the row is excluded either way. Every identifier is read from the
    recognized descriptor / AST (never hardcoded), so it generalizes to any query of this shape
    (q1/q30/q81/...). Returns None if the shape is not present (caller stays free-form)."""
    d = components.get("decorrelation")
    sql = components.get("sql")
    if not d or not sql:
        return None
    dialect = components.get("dialect", "postgres")
    try:
        ast = sqlglot.parse_one(sql, dialect=dialect)
    except Exception:
        return None
    target = None
    for cmp in ast.find_all(exp.GT, exp.LT, exp.GTE, exp.LTE, exp.EQ, exp.NEQ):
        for operand in (cmp.expression, cmp.this):
            if isinstance(operand, exp.Subquery) and operand.this.find(exp.AggFunc) is not None:
                target = operand
                break
        if target is not None:
            break
    if target is None:
        return None
    target.replace(exp.column("_v", _DECORR_CTE))
    precalc = (
        f"SELECT {d['inner_key']} AS _k, {d['agg_select']} AS _v "
        f"FROM {d['inner_table']} AS {d['inner_alias']} GROUP BY {d['inner_key']}"
    )
    try:
        # Attach the precomputation as a COMMA-JOIN + key-equality in WHERE — NOT an explicit
        # `... , t INNER JOIN _decorr ON outer.k = _decorr._k`: that binds the JOIN to the nearest
        # comma table and puts the other comma tables OUT OF SCOPE for the ON ("ctr1 cannot be
        # referenced from this part"). Comma-join + WHERE keeps every outer table in scope and is an
        # INNER join, matching the correlated subquery's semantics.
        ast.args.setdefault("joins", []).append(exp.Join(this=exp.table_(_DECORR_CTE)))
        ast = ast.where(f"{d['outer_key']} = {_DECORR_CTE}._k", append=True, dialect=dialect)
        ast = ast.with_(_DECORR_CTE, as_=precalc, dialect=dialect)
        return ast.sql(dialect=dialect)
    except Exception:
        return None


def _build_consolidation(backend, components: dict) -> str | None:
    """DETERMINISTIC consolidation transform (architecture A). Rewrites N repeated UNCORRELATED
    scalar-aggregate subqueries over one table into a SINGLE scan: a `_consol` CTE computes every
    aggregate with a per-bucket conditional (PG `FILTER (WHERE pred)`, MySQL `CASE WHEN pred`), and each
    original subquery is replaced by a reference to its precomputed column. The CTE has no GROUP BY -> one
    row -> cross-joined into the outer query (semantics preserved: `agg(x) FILTER (WHERE p)` equals
    `(SELECT agg(x) ... WHERE p)`). Dialect is resolved through the backend (bucket_agg_expr), NEVER
    branched here. Returns the rewritten SQL or None. The runtime S=1 gate is the equivalence backstop."""
    d = components.get("consolidation")
    ast = components.get("ast")
    if not d or ast is None:
        return None
    dialect = components.get("dialect", "postgres")
    try:
        cte_cols: dict[str, str] = {}   # alias -> conditional aggregate expr (deduped on alias)
        for b in d["buckets"]:
            if b["alias"] not in cte_cols:
                cte_cols[b["alias"]] = backend.bucket_agg_expr(b["agg"], b["pred"])
            b["node"].replace(exp.column(b["alias"], "_consol"))
        cte_select = (
            "SELECT " + ", ".join(f"{expr} AS {alias}" for alias, expr in cte_cols.items())
            + f" FROM {d['table']}"
        )
        ast.args.setdefault("joins", []).append(exp.Join(this=exp.table_("_consol")))
        ast = ast.with_("_consol", as_=cte_select, dialect=dialect)
        return ast.sql(dialect=dialect)
    except Exception:
        return None
