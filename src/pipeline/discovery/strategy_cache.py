"""#12 validated-strategy cache (Paper 2) — persist landed strategies + retrieve by SHAPE.

A reliable land (S=1 + gain) persists its {structural signature, strategy, technique, gain}. A future
query retrieves a cached strategy ONLY if its own signature CONTAINS the tags the technique requires —
so the cache proposes filter-early on q38 (same shape as q69) but NEVER on q85 (a flat join). The
retrieved strategy is then RE-VALIDATED (S=1 + gain) downstream; a hit that fails is demoted. This
module is the store + lookup; the architect bypass (piece 3) and the persist/reinforce hook (piece 4)
call into it.

Toggles (both inert => no lookup, no persist; pure discovery baseline):
  STRATEGY_CACHE=on          -> lookup/bypass enabled
  STRATEGY_CACHE_PERSIST=on  -> auto-write on land (defaults to STRATEGY_CACHE; set off for held-out:
                                build the cache on set A, freeze, test transfer on set B)
"""
import hashlib
import json
import os

from src.pipeline.discovery.structural_signature import structural_signature

CACHE_DIR = os.getenv("STRATEGY_CACHE_DIR", ".cache/strategy_cache")

# The tags that TARGET a technique (as opposed to generic descriptors). A cached entry's required-tags
# are the intersection of its source query's signature with these — the shape the technique attacks.
TAG_TO_TECHNIQUE = {
    "correlated_scalar_aggregate": "decorrelation",
    "repeated_scalar_aggregate": "consolidation",
    "repeated_filtered_dim_blocks": "filter_early_shared_dim",
}
TECHNIQUE_TAGS = frozenset(TAG_TO_TECHNIQUE)

MIN_PRECISION = 0.34  # below this hit-precision (lands/hits) an entry is evicted (auto-cure)
MIN_HITS_TO_JUDGE = 3  # ...but only after it has been tried enough times to judge


def enabled() -> bool:
    return os.getenv("STRATEGY_CACHE", "").strip().lower() in ("1", "true", "on", "yes")


def persist_enabled() -> bool:
    v = os.getenv("STRATEGY_CACHE_PERSIST", "").strip().lower()
    if v in ("1", "true", "on", "yes"):
        return True
    if v in ("0", "false", "off", "no"):
        return False
    return enabled()  # default: follow STRATEGY_CACHE


def _entry_id(technique: str, required: frozenset[str]) -> str:
    """Dedup key: one entry per (technique × required-shape). Re-landing the same technique on the
    same shape REINFORCES the existing entry instead of adding a near-duplicate (the coarse dedup;
    semantic dedup of the strategy TEXT is #10b)."""
    key = technique + "|" + "|".join(sorted(required))
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _path(entry_id: str) -> str:
    return os.path.join(CACHE_DIR, entry_id + ".json")


def _required_tags(sig: frozenset[str]) -> frozenset[str]:
    return frozenset(sig & TECHNIQUE_TAGS)


def save_landed(
    sql: str,
    strategy: str,
    technique: str | None = None,
    gain: float = 0.0,
    dialect: str = "postgres",
    force: bool = False,
) -> str | None:
    """Persist a landed strategy keyed by the shape it attacks. Returns the entry id, or None if the
    query has no technique-targeting shape (nothing to key on) or persistence is off (unless force).
    `technique` is derived from the query's shape when not given (auto-persist path). `force` bypasses
    the env gate for hand-seeding / tests."""
    if not force and not persist_enabled():
        return None
    sig = structural_signature(sql, dialect=dialect)
    required = _required_tags(sig)
    if not required:
        return None  # no recognizable shape to route on — don't cache a blind strategy
    if technique is None:  # derive from the primary shape tag (stable, 1:1 with the required tags)
        technique = TAG_TO_TECHNIQUE.get(sorted(required)[0], "unknown")
    eid = _entry_id(technique, required)
    os.makedirs(CACHE_DIR, exist_ok=True)
    existing = _load_entry(eid)
    if existing:  # reinforce: keep the best-gain strategy, bump counters
        existing["lands"] = existing.get("lands", 0) + 1
        existing["hits"] = existing.get("hits", 0) + 1
        if gain > existing.get("gain", 0):
            existing["strategy"], existing["gain"] = strategy, gain
        entry = existing
    else:
        entry = {
            "id": eid,
            "technique": technique,
            "required_tags": sorted(required),
            "signature": sorted(sig),
            "strategy": strategy,
            "gain": gain,
            "query_hash": hashlib.sha256(sql.encode()).hexdigest()[:12],
            "hits": 1,
            "lands": 1,
        }
    with open(_path(eid), "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2)
    return eid


def _load_entry(eid: str) -> dict | None:
    try:
        with open(_path(eid), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_all() -> list[dict]:
    if not os.path.isdir(CACHE_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(CACHE_DIR)):
        if fn.endswith(".json"):
            e = _load_entry(fn[:-5])
            if e:
                out.append(e)
    return out


def lookup(sql: str, dialect: str = "postgres") -> dict | None:
    """Return the best cached strategy whose REQUIRED shape the query satisfies, else None. Structural
    match is the decider (an embedding prefilter is a future scale optimization, never the arbiter).
    'Best' = highest gain × confidence(lands/hits). Inert (None) when STRATEGY_CACHE is off."""
    if not enabled():
        return None
    sig = structural_signature(sql, dialect=dialect)
    if not (sig & TECHNIQUE_TAGS):
        return None
    best, best_score = None, -1.0
    for e in load_all():
        req = frozenset(e.get("required_tags") or [])
        if req and req <= sig:  # the query HAS every tag this technique needs
            conf = (e.get("lands", 0) / e["hits"]) if e.get("hits") else 0.0
            score = (e.get("gain", 0.0)) * (conf or 1.0)
            if score > best_score:
                best, best_score = e, score
    return best


def reinforce(eid: str, landed: bool) -> None:
    """After a cached strategy is RE-VALIDATED downstream: bump hits, bump lands iff it landed, and
    evict if precision falls below the floor once judged (auto-cure)."""
    e = _load_entry(eid)
    if not e:
        return
    e["hits"] = e.get("hits", 0) + 1
    if landed:
        e["lands"] = e.get("lands", 0) + 1
    if e["hits"] >= MIN_HITS_TO_JUDGE and (e.get("lands", 0) / e["hits"]) < MIN_PRECISION:
        try:
            os.remove(_path(eid))
        except OSError:
            pass
        return
    with open(_path(eid), "w", encoding="utf-8") as f:
        json.dump(e, f, indent=2)
