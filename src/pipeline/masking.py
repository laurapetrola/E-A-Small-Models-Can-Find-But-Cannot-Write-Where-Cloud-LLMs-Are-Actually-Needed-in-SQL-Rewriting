"""Privacy masking for the CLOUD writer leg (P2/RQ5, Gate 3).

The FIND agent (architect) is LOCAL and trusted — it reads the real query, schema and plan.
Only the WRITE agent, when it runs in the CLOUD (WRITER_BACKEND=cloud), receives a payload that
leaves the machine. This module masks that payload with a DETERMINISTIC BIJECTIVE map so the cloud
coder never sees real identifiers or string literals: it rewrites the MASKED SQL, and we UNMASK the
result before the S=1 gate validates it against the REAL database.

Scope (MASK_SCOPE):
  - "id_str" (default): mask table/column/alias identifiers + STRING literals; keep numeric literals
                        (SQL always stays valid; numerics are the least sensitive).
  - "all":              additionally map numeric literals to sentinel numbers (stronger privacy,
                        risk the coder simplifies the sentinel — measured in the A/B).

The map is built from the ORIGINAL SQL's AST (sqlglot) and applied to every text field sent to the
cloud (SQL, NL strategy, schema block, history, errors). Placeholders are unique tokens
(tbl_N / col_N / 'litN' / 90000N) that survive the coder's transformations and reverse cleanly; any
NEW name the coder introduces (CTE names, fresh aliases) is not in the map and is left untouched.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp


def masking_enabled() -> bool:
    return os.getenv("MASKING", "").strip().lower() in ("1", "true", "on", "yes")


def mask_scope() -> str:
    return os.getenv("MASK_SCOPE", "id_str").strip().lower()


@dataclass
class MaskMap:
    """Bijective real<->placeholder maps for one query."""
    ids: dict[str, str] = field(default_factory=dict)        # identifier name -> placeholder name
    strs: dict[str, str] = field(default_factory=dict)       # string-literal value -> placeholder value
    nums: dict[str, str] = field(default_factory=dict)       # numeric-literal value -> sentinel (scope=all)

    def reverse(self) -> "MaskMap":
        return MaskMap(
            ids={v: k for k, v in self.ids.items()},
            strs={v: k for k, v in self.strs.items()},
            nums={v: k for k, v in self.nums.items()},
        )

    def is_empty(self) -> bool:
        return not (self.ids or self.strs or self.nums)


def build_mask_map(sql: str, dialect: str | None = None, scope: str | None = None) -> MaskMap:
    """Collect every identifier + literal from the ORIGINAL SQL and assign stable placeholders.
    First-seen order → deterministic (temperature-free, reproducible)."""
    scope = scope or mask_scope()
    ast = sqlglot.parse_one(sql, read=dialect)
    mm = MaskMap()

    # Which identifier names are TABLE names → prefix tbl_ (cosmetic; helps the coder read structure).
    table_names: set[str] = set()
    for t in ast.find_all(exp.Table):
        if isinstance(t.this, exp.Identifier):
            table_names.add(t.this.name)

    tc = cc = lc = nc = 0
    for node in ast.find_all(exp.Identifier):
        name = node.name
        if name in mm.ids:
            continue
        if name in table_names:
            tc += 1
            mm.ids[name] = f"tbl_{tc}"
        else:
            cc += 1
            mm.ids[name] = f"col_{cc}"

    for node in ast.find_all(exp.Literal):
        if node.is_string:
            val = node.name  # the raw string value (without quotes)
            if val not in mm.strs:
                lc += 1
                mm.strs[val] = f"lit{lc}"
        elif scope == "all":
            val = node.name
            if val not in mm.nums:
                nc += 1
                # sentinel numbers well outside typical ranges; kept numeric so the SQL stays valid
                mm.nums[val] = str(900000 + nc)
    return mm


def identifiers_in_schema_block(text: str) -> list[str]:
    """Every identifier a schema-linking block advertises, as `table(col, col, ...)` entries.

    The block is produced by the backend in that shape. Matching is deliberately strict — per LINE,
    and only when EVERY comma-separated item is a bare identifier. A loose `name(...)` scan over the
    whole text also swallows the block's own prose header ("AVAILABLE COLUMNS (these exist — use ONLY
    these, never invent a column)"), which would put English words into the mask map and let
    mask_text rewrite the instructions themselves.
    """
    names: list[str] = []
    ident = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
    for line in (text or "").splitlines():
        m = re.match(r"\s*[-*]?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*\Z", line)
        if not m:
            continue
        cols = [c.strip() for c in m.group(2).split(",") if c.strip()]
        if not cols or not all(ident.match(c) for c in cols):
            continue          # prose in the parentheses -> not a schema entry
        names.append(m.group(1))
        names.extend(cols)
    return names


def extend_mask_map(mm: MaskMap, names: Iterable[str]) -> MaskMap:
    """Add identifiers that are NOT in the query but WILL travel in the payload.

    WHY (leak found 2026-08-15, during validation of the writer benchmark): the mask map is built
    from the ORIGINAL SQL, but schema-linking advertises EVERY column of each relevant table. The
    columns the query does not use were absent from the map, so `mask_text` left them untouched and
    they reached the cloud in the clear — a median of 85 real identifiers per payload, 21/21 payloads
    affected, including `c_email_address` and `c_birth_country`, which disclose the data domain.
    The masked SQL itself was always clean; the hole was only ever the schema block.

    Worse, `find_leaks` could not see it: it only checks names that are IN the map, so an identifier
    that never entered the map is invisible to it. Extending the map therefore closes the leak AND
    restores the guarantee, because find_leaks then covers these names too.

    Placeholders continue the existing `col_N` numbering, so the map stays bijective and stable.
    """
    used = {int(m.group(1)) for ph in mm.ids.values()
            if (m := re.fullmatch(r"col_(\d+)", ph))}
    nxt = max(used, default=0)
    for name in names:
        if not name or name in mm.ids:
            continue
        nxt += 1
        mm.ids[name] = f"col_{nxt}"
    return mm


def mask_sql(sql: str, mm: MaskMap, dialect: str | None = None) -> str:
    """Rewrite the SQL's AST: identifiers -> placeholders, string (and optionally numeric) literals ->
    placeholders. Returns valid SQL in the same dialect."""
    ast = sqlglot.parse_one(sql, read=dialect)
    for node in ast.find_all(exp.Identifier):
        if node.name in mm.ids:
            node.set("this", mm.ids[node.name])
    for node in ast.find_all(exp.Literal):
        if node.is_string and node.name in mm.strs:
            node.set("this", mm.strs[node.name])
        elif (not node.is_string) and node.name in mm.nums:
            node.set("this", mm.nums[node.name])
    return ast.sql(dialect=dialect)


def _sub_words(text: str, mapping: dict[str, str]) -> str:
    """Word-boundary replace of each key -> value, LONGEST key first (so `sr_customer_sk` is replaced
    before `customer`). Used for NL text (strategy / schema / errors) sent to the cloud."""
    if not text or not mapping:
        return text
    for real in sorted(mapping, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(real)}\b", mapping[real], text)
    return text


def mask_text(text: str, mm: MaskMap) -> str:
    """Mask a free-text field (NL strategy, schema block, history, errors). Identifiers by word
    boundary; quoted string literals '<val>' -> '<placeholder>'."""
    if not text:
        return text
    out = _sub_words(text, mm.ids)
    for real, ph in sorted(mm.strs.items(), key=lambda kv: len(kv[0]), reverse=True):
        out = out.replace(f"'{real}'", f"'{ph}'")
    for real, ph in sorted(mm.nums.items(), key=lambda kv: len(kv[0]), reverse=True):
        out = re.sub(rf"\b{re.escape(real)}\b", ph, out)
    return out


def unmask(text: str, mm: MaskMap) -> str:
    """Reverse the mask on the SQL the cloud coder returned. Placeholders -> real names/values.
    New names the coder invented (not placeholders) are left as-is."""
    if not text:
        return text
    rev = mm.reverse()
    # identifiers: word-boundary (placeholders are tbl_N / col_N — safe tokens)
    out = _sub_words(text, rev.ids)
    # string literals: 'litN' -> '<real>'
    for ph, real in rev.strs.items():
        out = out.replace(f"'{ph}'", f"'{real}'")
    # numeric sentinels -> real number
    for ph, real in rev.nums.items():
        out = re.sub(rf"\b{re.escape(ph)}\b", real, out)
    return out


# ---- non-leak guarantee (peça c: prove the payload carries only structure) -----------------------

def find_leaks(payload: str, mm: MaskMap) -> list[str]:
    """Return any REAL identifier/literal that still appears in the masked payload (should be empty).
    This is the evidence for 'the cloud never sees sensitive data'."""
    leaks = []
    for real in mm.ids:
        if re.search(rf"\b{re.escape(real)}\b", payload):
            leaks.append(real)
    for real in mm.strs:
        if f"'{real}'" in payload:
            leaks.append(f"'{real}'")
    for real in mm.nums:
        if re.search(rf"\b{re.escape(real)}\b", payload):
            leaks.append(real)
    return leaks
