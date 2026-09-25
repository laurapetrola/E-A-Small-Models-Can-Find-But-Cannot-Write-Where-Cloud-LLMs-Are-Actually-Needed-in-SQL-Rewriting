"""#10b auto-feed — the GUIDED arm learns automatically (the #10a manual step, automated).

Wires the pieces that already exist into ONE opt-in batch loop:
  discover_clusters  →  curate (reliable land + teachable)  →  DISTILL a GENERAL rule  →
  SEMANTIC DEDUP vs already-injected rules  →  add_rule (guided arm sees it)

Design (from the #12 curve, 2026-07-13):
  - Feeds `learned_rules` (guided arm — the model ADAPTS the distilled rule), which transfers BETTER
    than the verbatim strategy the #12 cache bypasses with (q5: #12 verbatim regressed −21%; #10a
    distilled rule landed 43%). #12 = efficiency bypass; #10b = quality transfer. Complementary.
  - Promotes only RELIABLE lands (curation gate), never the 1/3 stochastic noise.
  - DEDUP semantically: the same technique phrased differently must NOT become two rules (bloats the
    prompt, dilutes the signal). Key on the rule's MEANING (embedding), not its text.

OFFLINE / DISCOVERY only (never the served path). Opt-in via AUTO_FEED=on so it never runs by accident
(it MUTATES learned_rules, which the guided arm reads).
"""
import os

import numpy as np

from src.pipeline.discovery.learned_rules import add_rule, load_rules
from src.pipeline.discovery.promotion import evaluate_discovery_criteria, evaluate_teachable

# Two rules whose distilled TEXT embeds this close are the same technique paraphrased → dedup.
DEDUP_THRESHOLD = float(os.getenv("AUTO_FEED_DEDUP_THRESHOLD", "0.88"))


def _enabled() -> bool:
    return os.getenv("AUTO_FEED", "").strip().lower() in ("1", "true", "on", "yes")


def _embedder():
    from langchain_ollama import OllamaEmbeddings
    return OllamaEmbeddings(
        model=os.getenv("EMBEDDING_MODEL", "nomic-embed-text"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )


def _normed(vecs: list) -> list:
    out = []
    for v in vecs:
        a = np.array(v)
        n = np.linalg.norm(a)
        out.append(a / n if n > 0 else a)
    return out


def _is_duplicate(candidate: str, existing: list[str], embed=None) -> bool:
    """Is `candidate` semantically a paraphrase of any already-injected rule? Embedding cosine ≥
    DEDUP_THRESHOLD → duplicate. Empty `existing` → never a duplicate. `embed` injectable for tests."""
    existing = [e for e in existing if e]
    if not existing:
        return False
    embed = embed or (lambda texts: _embedder().embed_documents(texts))
    vecs = _normed(embed([candidate] + existing))
    cand, others = vecs[0], vecs[1:]
    return any(float(np.dot(cand, o)) >= DEDUP_THRESHOLD for o in others)


def _slug(text: str, n: int = 40) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:n] or "rule"


# ⛔ VAZAMENTO DE BENCHMARK (21/08). Uma regra que carrega LITERAL ou NOME DE TABELA do benchmark
# ensina o modelo a decorar o TPC-DS/JOB e contamina o held-out, que é a nossa prova
# anti-memorização. O relatório dry-run de 20/08 trazia DUAS assim entre as 11 candidatas:
#   "pre-filter CTE with ca_state = 'TN'"  ·  "replace IN subquery with JOIN to date_dim_filtered"
# A regra tem de falar da FORMA da query, nunca destes dados.
_BENCH_TOKENS = ("store_sales", "web_sales", "catalog_sales", "date_dim", "customer_demographics",
                 "store_returns", "web_returns", "customer_address", "household_demographics",
                 "cast_info", "movie_", "aka_", "d_year", "d_moy", "ss_", "ws_", "cs_", "ca_",
                 "cd_", "i_item", "_sk")


def _leaks_benchmark(hint: str) -> bool:
    import re as _re
    h = (hint or "").lower()
    if any(t in h for t in _BENCH_TOKENS):
        return True
    return bool(_re.search(r"=\s*'[^']+'|=\s*\d{3,}", h))   # literal de comparação


def auto_feed(backend, clusters=None, model_family: str | None = None,
              distill=None, embed=None, force: bool = False, dry_run: bool = False) -> list[dict]:
    """Run one auto-feed pass. Returns a per-cluster report (status: injected | would_inject (dry-run) |
    skipped_duplicate | rejected_criteria | not_teachable | no_hint). Inert unless AUTO_FEED=on (or
    force=True for tests). `dry_run` reports what WOULD be injected without touching learned_rules.
    `clusters`/`distill`/`embed` are injectable so the orchestration is testable without LLM/DB/embeddings.
    """
    if not force and not _enabled():
        return [{"status": "disabled"}]

    if clusters is None:
        from src.pipeline.backends.relational.heuristic_discoverer import discover_clusters
        clusters = discover_clusters()
    if distill is None:
        from src.pipeline.backends.relational.heuristic_discoverer import generate_teaching_hint
        distill = lambda c: generate_teaching_hint(c, model_family=model_family)

    report: list[dict] = []
    accepted_hints: list[str] = []   # o que JÁ foi aceito NESTE lote — para o dedup intra-lote
    for c in clusters:
        rep = c.get("representative")
        # RELIABILITY gate (curation): the technique landed reliably with real gain (not 1/3 noise).
        crit = evaluate_discovery_criteria(c)
        if not crit.passed:
            report.append({"representative": rep, "status": "rejected_criteria", "reasons": crit.reasons})
            continue
        # TEACHABLE gate: at least one model LANDED it (worked example to distill from) AND at least one
        # genuinely reached-but-missed (a student the rule helps). Skips detector-only / already-universal.
        teach = evaluate_teachable(c, backend)
        if not teach.teachable:
            report.append({"representative": rep, "status": "not_teachable", "reasons": teach.reasons})
            continue
        # DISTILL a GENERAL rule (no table/column literals) from the landed before→after examples.
        hint = distill(c)
        if not hint:
            report.append({"representative": rep, "status": "no_hint"})
            continue
        # SEMANTIC DEDUP: don't add a paraphrase of a rule already in learned_rules.
        # ⛔ vazamento de benchmark — ver `_leaks_benchmark`
        if _leaks_benchmark(hint):
            report.append({"representative": rep, "status": "rejected_benchmark_leak", "hint": hint})
            continue
        if _is_duplicate(hint, list(load_rules().values()), embed=embed):
            report.append({"representative": rep, "status": "skipped_duplicate", "hint": hint})
            continue
        # ⛔ DEDUP DENTRO DO LOTE (21/08). O dedup acima só olha as regras JÁ injetadas, nunca
        # candidata contra candidata — por isso `materialize CTE to avoid recomputation` e
        # `materilize cte to avoid recomputation` (com typo) passaram AS DUAS no dry-run de 20/08.
        if _is_duplicate(hint, accepted_hints, embed=embed):
            report.append({"representative": rep, "status": "skipped_duplicate_in_batch", "hint": hint})
            continue
        name = _slug(rep or hint)
        if dry_run:
            accepted_hints.append(hint)
            accepted_hints.append(hint)
            report.append({"representative": rep, "status": "would_inject", "name": name, "hint": hint,
                           "tier": crit.tier, "students": teach.student_models})
            continue
        add_rule(name, hint)
        accepted_hints.append(hint)
        report.append({"representative": rep, "status": "injected", "name": name, "hint": hint,
                       "tier": crit.tier, "students": teach.student_models})
    return report
