import json
import logging
import re

log = logging.getLogger(__name__)

# Post-hoc, observability-only. This prompt is NEVER part of the rewrite path — it runs
# AFTER a free-form rewrite is approved (hash-validated), so it cannot change the SQL the
# system produces. Its only job is to name the technique(s) the LLM applied in free-form,
# so successful free-form transformations feed the Direction B→A discovery loop (which
# otherwise only sees `suggestions`, i.e. things the structured path FAILED to do).
#
# This is POST-HOC NAMING, not the rewrite — it runs AFTER the rewrite to canonicalize what the
# model already did, so a generic example menu here is for naming CONSISTENCY (better clustering),
# NOT a bias on HOW to optimize (that bias lives in the rewrite prompt, which is kept neutral). The
# menu is generic SQL vocabulary with a "use only if it actually applies" guard, and cosmetic-only
# rewrites must return [] (avoids noise like "alias formatting"). Benchmark-agnostic: never the
# query's table/column names. The convergence signal still comes from the REWRITES, not the menu.
_PROMPT = """The OPTIMIZED query below is a semantically equivalent rewrite of the ORIGINAL \
(verified by result-set hash). Name the SQL optimization technique(s) the rewrite applied, \
as short GENERIC phrases describing the transformation — never the specific tables/columns."""

# Unverified variant: the rewrite was REJECTED (invalid SQL, non-equivalent result, or no gain).
# We are not claiming it is correct — only naming the technique it was TRYING to apply, so the
# discovery loop can see what the model reached for even when it could not land the SQL. This is
# the signal that distinguishes "model can't reason this" from "model reasons it but botches the
# mechanics" (a hint/repair candidate, not a model limit).
_PROMPT_ATTEMPT = """The OPTIMIZED query below is an ATTEMPT to rewrite the ORIGINAL for better \
performance. It was NOT accepted — it may be syntactically invalid, non-equivalent, or simply no \
faster. Do NOT assume it is correct. Name the SQL optimization technique(s) the rewrite was TRYING \
to apply, as short GENERIC phrases describing the intended transformation — never the specific \
tables/columns."""

_PROMPT_TAIL = """
Use short, generic names (generic SQL vocabulary, never this query's table/column names). Examples \
of the STYLE — use ONLY if they actually apply, do not force a match:
  - "decorrelate scalar aggregate subquery into CTE plus join"
  - "pre-filter a large table as a CTE before the join"
  - "reorder joins to reduce intermediate rows"
  - "convert an IN subquery to a semi-join"
  - "split an OR predicate into UNION ALL branches"
CRITICAL — name only what is VISIBLY DIFFERENT between ORIGINAL and OPTIMIZED. You are naming what \
the rewrite DID, never what the original could benefit from. Compare the two queries directly: if a \
construct appears UNCHANGED in both (e.g. a correlated subquery `> (SELECT AVG(...) ... WHERE \
outer.key = inner.key)` is STILL PRESENT in the OPTIMIZED), then that technique was NOT applied — do \
NOT name it. If the only differences are cosmetic (whitespace, alias casing, clause/table order) with \
no structural transform, return [].

Return ONLY a JSON array of strings.

ORIGINAL:
{original}

OPTIMIZED:
{optimized}
"""


def label_freeform_techniques(original_sql: str, optimized_sql: str, llm, verified: bool = True) -> list[str]:
    """Ask the LLM to name the technique(s) a free-form rewrite applied (verified=True, approved)
    or was trying to apply (verified=False, rejected attempt).

    Returns generic technique phrases for the Direction B→A discovery feed. Pure
    observability — does NOT touch the rewrite prompt. Fail-open: returns [] on any error,
    so it can never break or alter the pipeline.
    """
    try:
        from langchain_core.messages import HumanMessage
        head = _PROMPT if verified else _PROMPT_ATTEMPT
        prompt = (head + _PROMPT_TAIL).format(original=original_sql, optimized=optimized_sql)
        resp = llm.invoke([HumanMessage(content=prompt)])
        content = resp.content or ""
        # Strip any reasoning block first: a long-CoT model wraps its answer after <think>...</think>,
        # and a greedy [.*] would span INTO that block (arrays mentioned mid-reasoning) → json.loads fails
        # → empty labels (the FIND is silently lost). Belt-and-suspenders even with reasoning=False on.
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        # Take the LAST flat JSON array — the final answer follows any preamble.
        matches = re.findall(r"\[[^\[\]]*\]", content, re.DOTALL)
        if not matches:
            return []
        labels = json.loads(matches[-1])
        if not isinstance(labels, list):
            return []
        return [str(x).strip() for x in labels if str(x).strip()][:5]
    except Exception as e:
        log.info(f"[freeform_labeler] failed (fail-open, no effect): {e}")
        return []
