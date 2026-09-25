import os

from src.pipeline.backends.base import QueryBackend
from src.pipeline.backends.relational.postgres import PostgresBackend
from src.pipeline.backends.relational.mysql import MySQLBackend
from src.pipeline.backends.graph.neo4j import Neo4jBackend

_REGISTRY: dict[str, type] = {
    "postgres": PostgresBackend,
    "mysql": MySQLBackend,
    "neo4j": Neo4jBackend,
    # "mongo": MongoBackend,
}

# URI scheme prefix → DB_TYPE mapping for auto-detection
_URI_PREFIXES: list[tuple[str, str]] = [
    ("postgresql", "postgres"),
    ("postgres",   "postgres"),
    ("mysql",      "mysql"),
    ("neo4j",      "neo4j"),
]


def get_db_type() -> str:
    """Resolve the active backend's db_type from DB_TYPE, else the DB_URI scheme, else 'postgres'.
    Exposed so callers (e.g. the discovery store) can TAG records by engine — the same query run on
    PostgreSQL and MySQL shares a query_hash, so without this they'd pool into one cluster."""
    db_type = os.getenv("DB_TYPE", "").strip().lower()
    if not db_type:
        uri = os.getenv("DB_URI", "")
        for prefix, detected in _URI_PREFIXES:
            if uri.startswith(prefix):
                return detected
        return "postgres"  # safe default
    return db_type


def get_backend() -> QueryBackend:
    db_type = get_db_type()

    cls = _REGISTRY.get(db_type)
    if cls is None:
        raise ValueError(
            f"Unknown DB_TYPE '{db_type}'. "
            f"Registered backends: {list(_REGISTRY)}. "
            f"Set DB_TYPE in .env or use a recognized DB_URI scheme."
        )
    return cls()
