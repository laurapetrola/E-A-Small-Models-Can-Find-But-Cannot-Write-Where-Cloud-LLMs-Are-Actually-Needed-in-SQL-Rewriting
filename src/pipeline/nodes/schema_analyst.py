import json
import logging
import os
import shutil
import time
import psutil
from src.pipeline.backends import get_backend
from src.pipeline.state import PipelineState

CACHE_FILE = ".cache/schema_cache.json"

log = logging.getLogger(__name__)

_schema_cache: dict | None = None


def _clear_semantic_cache() -> None:
    from src.pipeline.cache.semantic_cache import CACHE_DIR
    if os.path.exists(CACHE_DIR):
        shutil.rmtree(CACHE_DIR)
        log.info("[schema_analyst] semantic cache cleared — cached SQL optimizations may be stale after schema change")


def invalidate_cache():
    global _schema_cache
    _schema_cache = None
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    _clear_semantic_cache()


def _load_from_disk() -> dict | None:
    if not os.path.exists(CACHE_FILE):
        return None
    with open(CACHE_FILE) as f:
        return json.load(f)


def _save_to_disk(cache: dict):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)


def _hardware_snapshot(backend) -> dict:
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "ram_total_gb": round(ram.total / 1e9, 1),
        "ram_available_gb": round(ram.available / 1e9, 1),
        "disk_total_gb": round(disk.total / 1e9, 1),
        "disk_free_gb": round(disk.free / 1e9, 1),
        "db_latency_ms": backend.ping_latency(),
    }


def _build_system_prompt(tables: list[dict], relations: list[dict], hw: dict) -> str:
    lines = ["=== DATABASE SCHEMA ==="]

    for t in tables:
        lines.append(f"\nTable: {t['name']} (rows: {t['row_count']:,})")

        for c in t["columns"]:
            tags = []
            if c.get("primary_key"):
                tags.append("PK")
            if c.get("foreign_key"):
                tags.append(f"FK → {c['foreign_key']}")
            if not c.get("nullable", True):
                tags.append("NOT NULL")
            tag_str = f"  [{', '.join(tags)}]" if tags else ""
            lines.append(f"    {c['name']}  {c['type']}{tag_str}")

        if t.get("primary_key"):
            lines.append(f"  Primary Key: ({', '.join(t['primary_key'])})")

        for idx in t.get("indexes", []):
            kind = "UNIQUE INDEX" if idx["unique"] else "INDEX"
            predicate = f" WHERE {idx['predicate']}" if idx.get("predicate") else ""
            lines.append(f"  {kind}: {idx['name']} on ({', '.join(idx['columns'])}){predicate}")

        for uc in t.get("unique_constraints", []):
            lines.append(f"  UNIQUE CONSTRAINT: {uc['name']} on ({', '.join(uc['columns'])})")

    if relations:
        lines.append("\n=== FOREIGN KEY RELATIONS ===")
        for r in relations:
            lines.append(
                f"  {r['from_table']}.({', '.join(r['from_columns'])}) "
                f"→ {r['to_table']}.({', '.join(r['to_columns'])})"
            )

    lines.append("\n=== HARDWARE SNAPSHOT ===")
    lines.append(f"  RAM     : {hw['ram_total_gb']} GB total | {hw['ram_available_gb']} GB available")
    lines.append(f"  Disk    : {hw['disk_total_gb']} GB total | {hw['disk_free_gb']} GB free")
    lines.append(f"  DB Latency: {hw['db_latency_ms']} ms")

    return "\n".join(lines)


def _build_cache() -> dict:
    backend = get_backend()
    tables, relations = backend.extract_schema()
    hw = _hardware_snapshot(backend)
    system_prompt = _build_system_prompt(tables, relations, hw)
    return {
        "schema_context": {"tables": tables, "relations": relations},
        "hardware_snapshot": hw,
        "system_prompt": system_prompt,
    }


def schema_analyst(state: PipelineState) -> PipelineState:
    global _schema_cache

    backend = get_backend()

    if _schema_cache is None:
        _schema_cache = _load_from_disk()

    # Fingerprint check — one cheap query to detect DDL changes (new/dropped indexes)
    if _schema_cache is not None:
        current_fp = backend.schema_fingerprint()
        cached_fp = _schema_cache.get("schema_fingerprint")
        if current_fp is not None and cached_fp is not None and current_fp != cached_fp:
            log.info("[schema_analyst] schema fingerprint changed — invalidating schema cache and semantic cache")
            _schema_cache = None
            _clear_semantic_cache()
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)

    if _schema_cache is None:
        # O cache é REGENERÁVEL: se o arquivo some, reconstrói do banco. Mas se o rebuild falhar
        # (DB indisponível/lento/auth), NÃO derrubar o nó com um crash críptico (0s, nó "?") — logar
        # a causa real e levantar um erro ACIONÁVEL. Caminho feliz (rebuild OK) fica idêntico.
        try:
            _schema_cache = _build_cache()
            _schema_cache["schema_fingerprint"] = backend.schema_fingerprint()
            _save_to_disk(_schema_cache)
        except Exception as e:
            log.error("[schema_analyst] cache %s ausente e REBUILD do banco FALHOU: %s",
                      CACHE_FILE, e, exc_info=True)
            raise RuntimeError(
                f"schema_analyst: {CACHE_FILE} ausente e não deu pra reconstruir do banco "
                f"({type(e).__name__}: {e}). O cache é regenerável — confira a conexão (DB_URI) "
                f"ou restaure o arquivo. NÃO é um limite do modelo."
            ) from e

    # Exclude internal fingerprint key from pipeline state
    state_update = {k: v for k, v in _schema_cache.items() if k != "schema_fingerprint"}
    return {
        **state,
        "status": "schema_analyzed",
        **state_update,
    }
