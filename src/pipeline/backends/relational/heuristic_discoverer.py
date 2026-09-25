import os
from collections import Counter

import numpy as np

from src.pipeline.discovery.suggestion_store import load_all

CLUSTER_THRESHOLD = float(os.getenv("CLUSTER_THRESHOLD", "0.80"))  # lower than dedup threshold to catch phrasing variations; env-tunable to merge paraphrases (e.g. FILTER vs CASE = same consolidation)
MIN_FREQUENCY = 4
BLOCKED_MAX_IMPROVEMENT = 1.0  # avg_improvement below this in runs where suggestion appears = vocabulary gap


def _embedder():
    from langchain_ollama import OllamaEmbeddings
    return OllamaEmbeddings(
        model=os.getenv("EMBEDDING_MODEL", "nomic-embed-text"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )


def discover_clusters(min_frequency: int = MIN_FREQUENCY) -> list[dict]:
    """
    Load all stored suggestion records, cluster by cosine similarity,
    rank by frequency × correlation delta.
    """
    records = load_all()
    if not records:
        return []

    # Schema-change keywords that should never become Direction A heuristics
    _SCHEMA_KEYWORDS = ("index", "index on", "gin index", "full-text", "materialized view", "partition")

    def _is_schema_change(text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in _SCHEMA_KEYWORDS)

    # Flatten to (text, improvement_pct, record_index) — skip schema change suggestions
    pairs: list[tuple[str, float | None, int]] = []
    for i, rec in enumerate(records):
        for s in rec.get("suggestions", []):
            if not _is_schema_change(s):
                pairs.append((s, rec.get("improvement_pct"), i))

    if not pairs:
        return []

    texts = [p[0] for p in pairs]
    raw_embeddings = _embedder().embed_documents(texts)
    embeddings = [np.array(e) for e in raw_embeddings]

    # Normalize
    normed: list[np.ndarray] = []
    for emb in embeddings:
        n = np.linalg.norm(emb)
        normed.append(emb / n if n > 0 else emb)

    # Greedy clustering
    clusters: list[dict] = []
    for text, imp_pct, rec_idx, emb_n in zip(texts, [p[1] for p in pairs], [p[2] for p in pairs], normed):
        placed = False
        for cluster in clusters:
            if np.dot(emb_n, cluster["centroid"]) >= CLUSTER_THRESHOLD:
                cluster["members"].append((text, imp_pct, rec_idx))
                n = len(cluster["members"])
                cluster["centroid"] = (cluster["centroid"] * (n - 1) + emb_n) / n
                norm = np.linalg.norm(cluster["centroid"])
                if norm > 0:
                    cluster["centroid"] /= norm
                placed = True
                break
        if not placed:
            clusters.append({"centroid": emb_n.copy(), "members": [(text, imp_pct, rec_idx)]})

    all_run_indices = {p[2] for p in pairs}
    scored: list[dict] = []

    for cluster in clusters:
        run_indices = {m[2] for m in cluster["members"]}
        frequency = len(run_indices)
        if frequency < min_frequency:
            continue

        imp_with = [records[i].get("improvement_pct") or 0.0 for i in run_indices]
        imp_without = [records[i].get("improvement_pct") or 0.0 for i in (all_run_indices - run_indices)]

        avg_with = float(np.mean(imp_with)) if imp_with else 0.0
        avg_without = float(np.mean(imp_without)) if imp_without else 0.0
        correlation_delta = avg_with - avg_without

        # A cluster is "blocked" when it appears frequently but the runs where it
        # appears never achieve meaningful improvement — signal that the current
        # heuristic vocabulary cannot express this optimization, not that it's wrong.
        blocked = frequency >= min_frequency and avg_with < BLOCKED_MAX_IMPROVEMENT

        # Outcome distribution across the runs where this technique appeared (None for
        # legacy records written before outcome tracking — they simply don't contribute).
        outcome_counts = dict(Counter(
            o for i in run_indices if (o := records[i].get("outcome"))
        ))
        landed = outcome_counts.get("rewrite_correct", 0)
        failed = (
            outcome_counts.get("mechanics_failed", 0)
            + outcome_counts.get("equivalence_failed_structural", 0)
            + outcome_counts.get("equivalence_failed_semantic", 0)
        )

        # "unrealized": the model reaches for this technique often but rarely lands valid,
        # equivalent SQL (failures >= successes, and there is at least one failure). The idea
        # is there; the mechanics are not. This is the prime candidate for a targeted hint /
        # deterministic repair (or, later, a decomposed transform) — and it is exactly the
        # case that used to look like a "model limit" because the failed attempts were dropped.
        unrealized = frequency >= min_frequency and failed > 0 and failed >= landed

        # Cross-model breadth (criterion b): distinct models that REACHED for the technique
        # (any outcome — a strong model landing it and a weak model botching it both count).
        models = sorted({m for i in run_indices if (m := records[i].get("model"))})

        # Gain when LANDED (criterion c): improvement measured only on runs that produced a valid,
        # equivalent rewrite (rewrite_correct). In a heterogeneous matrix the strong model usually
        # supplies this signal while the weak model supplies the cross-model breadth above.
        landed_imps = [records[i].get("improvement_pct") or 0.0
                       for i in run_indices if records[i].get("outcome") == "rewrite_correct"]
        landed_avg_improvement = float(np.mean(landed_imps)) if landed_imps else 0.0

        # Worked examples: the VALID rewrites (LLM-authored, hash-verified) from the LANDED runs of
        # this cluster. They are the concrete before→after a human studies when hand-writing the
        # deterministic detector (richer than the label phrasing alone) — never reused as raw SQL.
        sql_examples = [
            {"before": records[i].get("raw_sql"), "after": records[i]["optimized_sql"]}
            for i in sorted(run_indices)
            if records[i].get("outcome") == "rewrite_correct" and records[i].get("optimized_sql")
        ][:3]

        # Per-run reach records (every member, landed or not) — the teachable gate (degrau 1, hint-B)
        # uses these to verify a reach is GENUINE (SQL parses + real restructure), so a weak model's
        # HALLUCINATED label on broken SQL is not counted as "another model reached it". See
        # promotion.evaluate_teachable. Distinct from sql_examples (LANDED before→after for the teacher).
        reach_records = [
            {"model": records[i].get("model"), "raw_sql": records[i].get("raw_sql"),
             # reached_sql = best attempt (worked OR furthest botched-but-parseable); fall back to
             # optimized_sql for legacy records written before the field existed.
             "reached_sql": records[i].get("reached_sql") or records[i].get("optimized_sql"),
             "outcome": records[i].get("outcome")}
            for i in sorted(run_indices)
        ]

        # Promotion PRIORITY (which candidate a human should formalize FIRST): impact × coverage.
        #   impact   = landed_avg_improvement (proven real-time gain — magnitude, not a flat threshold)
        #   coverage = distinct query SHAPES that reached this technique (a PROXY for how many workload
        #              queries the future detector would fire on). True coverage needs the recognizer run
        #              over the workload, which only exists AFTER formalization (chicken-and-egg) — so
        #              pre-formalization we approximate with distinct query_hashes seen. See formulas §18.
        query_shapes = len({records[i].get("query_hash") for i in run_indices if records[i].get("query_hash")})
        priority_score = round(landed_avg_improvement * query_shapes, 1)

        scored.append({
            "representative": cluster["members"][0][0],
            "examples": [m[0] for m in cluster["members"][:5]],
            "sql_examples": sql_examples,
            "reach_records": reach_records,
            "query_shapes": query_shapes,
            "priority_score": priority_score,
            "frequency": frequency,
            "reach": frequency,
            "models": models,
            "model_count": len(models),
            "avg_improvement": avg_with,
            "landed_avg_improvement": landed_avg_improvement,
            "correlation_delta": correlation_delta,
            "score": frequency * max(0.0, correlation_delta),
            "blocked": blocked,
            "outcome_counts": outcome_counts,
            "unrealized": unrealized,
        })

    return sorted(scored, key=lambda x: x["score"], reverse=True)


# ---------------------------------------------------------------------------
# Teaching hint (the "qwen-professor"): a CAPABLE model writes a hint-B for the weaker ones
# ---------------------------------------------------------------------------

def generate_teaching_hint(cluster: dict, model_family: str | None = None) -> str | None:
    """The TEACHER (e.g. qwen — the model that LANDED the technique) writes a SHORT, GENERIC instruction
    (hint-B) teaching another model WHEN a technique applies and WHAT to do, from the before→after worked
    examples. For the GUIDED arm (degrau 1): does a strong model's hint make a WEAKER model land the
    technique in free-form (the model self-corrects the SQL via the deterministic reflection hints — there
    is NO separate coder agent anymore)? Injected via learned_rules.add_rule. OFFLINE / DISCOVERY only —
    never the served path (production = the LLM advisor itself; the hint is purely the guided-arm
    experiment). Agnostic BY INSTRUCTION (no table/column literals); the human reviews before activating.
    Returns the hint text or None.

    Default teacher = qwen-reasoning (configurable via TEACHER_MODEL_FAMILY) — the capable discoverer,
    NOT a coder: writing teaching guidance is reasoning, not code."""
    import re
    from src.connections import get_llm

    examples = cluster.get("sql_examples") or []
    pairs = "\n\n".join(
        f"BEFORE:\n{p['before']}\nAFTER:\n{p['after']}"
        for p in examples if p.get("before") and p.get("after")
    )
    if not pairs:
        return None

    prompt = (
        "These BEFORE→AFTER SQL rewrites all apply the SAME optimization technique, verified to return "
        "identical results:\n\n"
        f"{pairs}\n\n"
        "Write a SHORT instruction (2-3 sentences) that teaches another SQL optimizer:\n"
        "1. the STRUCTURAL PATTERN to recognize (when this technique applies), and\n"
        "2. WHAT transformation to perform (keeping the result set identical).\n"
        "Be GENERIC: describe the STRUCTURE only — NEVER use a specific table or column name from the "
        "examples (another optimizer will see different schemas). Output ONLY the instruction."
    )
    try:
        llm = get_llm(model_family or os.getenv("TEACHER_MODEL_FAMILY", "qwen-reasoning"))
        text = (llm.invoke(prompt).content or "")
        text = re.sub(r"<think>.*?</think>", " ", text, flags=re.DOTALL | re.IGNORECASE).strip()
        return text or None
    except Exception:
        return None


def write_and_inject_hint(cluster: dict, heuristic_name: str, model_family: str | None = None) -> str | None:
    """Convenience: the teacher writes the hint (generate_teaching_hint) and it is injected into the
    free-form prompt (learned_rules.add_rule) so APPLY_HEURISTICS=on guides the weaker model in
    DISCOVERY. Returns the hint, or None if the teacher produced nothing."""
    hint = generate_teaching_hint(cluster, model_family=model_family)
    if not hint:
        return None
    from src.pipeline.discovery.learned_rules import add_rule
    add_rule(heuristic_name, hint)
    return hint
