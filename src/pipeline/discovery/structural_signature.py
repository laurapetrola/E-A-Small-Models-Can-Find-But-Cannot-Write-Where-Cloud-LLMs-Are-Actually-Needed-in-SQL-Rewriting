"""Structural signature of a SQL query (#12 validated-strategy cache — Paper 2).

Maps a query to a SET OF SHAPE TAGS — the structural features that optimization techniques ATTACK,
never table/column literals (so it is benchmark-agnostic). This is the PRECISE half of the cache's
retrieval: the embedding is a coarse filter ("looks similar"), but only a structural match answers
"does this optimization APPLY here?". A cached technique stores the tag it REQUIRES; a new query is a
candidate only if its signature CONTAINS that tag (and lacks any tag the technique forbids).

Reuses the existing deterministic recognizers (sql_rewriter) for the two shapes the system already
detects (decorrelation, consolidation) and adds the repeated-filtered-block shape (the #10a target:
q69/q5/q38 filter-early on a shared dimension) — which is exactly what SEPARATES q69 (repeated EXISTS
blocks over a filtered date_dim → landed) from q85 (a single flat join → the technique regressed).
"""
import sqlglot
from sqlglot import exp

from src.pipeline.backends.relational.sql_rewriter import (
    detect_correlated_scalar_aggregate,
    detect_repeated_scalar_aggregates,
)

# ≥ this many repeated filtered blocks (EXISTS / IN / scalar subqueries / set-op branches) → the
# "filter-early on a shared dimension" shape. q69 has 3 EXISTS blocks; q85 (flat join) has 0.
MIN_FILTERED_BLOCKS = 2
MANY_TABLES = 4  # a "wide" join (heuristic; the shape, not a stats claim about table size)


def _has_literal_filter(select: exp.Select) -> bool:
    """Does this block's WHERE carry a SELECTIVE predicate (column OP literal / BETWEEN / IN-literals)?
    A join equality (column = column) does NOT count — we want a value filter, the thing worth pushing
    down. Only the block's OWN where (nested subqueries are handled as their own blocks)."""
    where = select.args.get("where")
    if where is None:
        return False
    for cmp in where.find_all(exp.EQ, exp.GT, exp.LT, exp.GTE, exp.LTE, exp.NEQ):
        sides = (cmp.this, cmp.expression)
        has_col = any(isinstance(s, exp.Column) for s in sides)
        has_lit = any(isinstance(s, exp.Literal) for s in sides)
        if has_col and has_lit:
            return True
    for between in where.find_all(exp.Between):
        if isinstance(between.this, exp.Column):
            return True
    for isin in where.find_all(exp.In):
        if isin.args.get("expressions") and all(
            isinstance(e, exp.Literal) for e in isin.args["expressions"]
        ):
            return True
    return False


def _n_tables(select: exp.Select) -> int:
    """Distinct base tables that belong to THIS select's scope — a table whose NEAREST enclosing Select
    is `select` itself (so tables in deeper nested subqueries are not counted). Robust to how sqlglot
    lays out comma-joins (FROM a, b) vs explicit JOINs — both put the Table nodes in this scope."""
    names = set()
    for t in select.find_all(exp.Table):
        anc = t.parent
        while anc is not None and not isinstance(anc, exp.Select):
            anc = anc.parent
        if anc is select:
            names.add((t.alias or t.name).lower())
    return len(names)


def _candidate_blocks(ast: exp.Expression) -> list[exp.Select]:
    """The 'blocks' whose repetition signals the shared-dimension shape: every NON-ROOT Select — i.e.
    nested inside a subquery (scalar / IN), an EXISTS, a CTE, or a branch of a set operation. The outer
    query's own Select is excluded (it is not a repeated block). Walk each Select's parent chain so
    EXISTS-inner and CTE selects are caught (they are not wrapped in exp.Subquery)."""
    blocks: list[exp.Select] = []
    for s in ast.find_all(exp.Select):
        p = s.parent
        while p is not None:
            if isinstance(p, (exp.Subquery, exp.Exists, exp.In, exp.CTE,
                              exp.Union, exp.Intersect, exp.Except)):
                blocks.append(s)
                break
            p = p.parent
    return blocks


def _repeated_filtered_blocks(ast: exp.Expression) -> bool:
    """≥ MIN_FILTERED_BLOCKS blocks that EACH join multiple tables AND carry a literal filter — the
    q69/q5/q38 'filter-early on a shared dimension' shape. q85 (single flat join) has 0 such blocks."""
    qualifying = [
        b for b in _candidate_blocks(ast)
        if _n_tables(b) >= 2 and _has_literal_filter(b)
    ]
    return len(qualifying) >= MIN_FILTERED_BLOCKS


def structural_signature(sql: str, dialect: str = "postgres") -> frozenset[str]:
    """The query's shape as a set of tags. Empty set = no recognized optimizable shape (falls through
    to fresh reasoning in the cache). NEVER raises — a parse failure yields an empty signature."""
    tags: set[str] = set()
    try:
        ast = sqlglot.parse_one(sql, dialect=dialect)
    except Exception:
        return frozenset()
    if ast is None:
        return frozenset()

    # --- technique-targeting shapes (reuse the existing deterministic recognizers) ---
    if detect_correlated_scalar_aggregate(sql, dialect=dialect) is not None:
        tags.add("correlated_scalar_aggregate")      # q1 → DECORRELATION
    if detect_repeated_scalar_aggregates(sql, dialect=dialect) is not None:
        tags.add("repeated_scalar_aggregate")        # q9 → CONSOLIDATION
    if _repeated_filtered_blocks(ast):
        tags.add("repeated_filtered_dim_blocks")     # q69/q5/q38 → FILTER-EARLY shared dim

    # --- generic descriptors (context / equivalence-risk flags) ---
    if any(ast.find_all(exp.Union, exp.Intersect, exp.Except)):
        tags.add("has_set_operation")
    if ast.find(exp.Exists) is not None:
        tags.add("has_exists_subquery")
    if any(isinstance(i.args.get("query"), exp.Subquery) or i.find(exp.Select) for i in ast.find_all(exp.In)):
        tags.add("has_in_subquery")
    if ast.find(exp.Distinct) is not None:
        tags.add("has_distinct")
    if ast.find(exp.Group) is not None:
        tags.add("has_group_by")
    if any(j.args.get("side") for j in ast.find_all(exp.Join)):
        tags.add("has_outer_join")
    root = ast.find(exp.Select)
    if root is not None and _n_tables(root) >= MANY_TABLES:
        tags.add("many_tables")

    return frozenset(tags)
