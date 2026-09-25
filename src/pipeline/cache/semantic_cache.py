import os
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings
from langchain_core.documents import Document

CACHE_DIR = ".cache/semantic_cache"
SUCCESS_SIMILARITY_THRESHOLD = 0.99  # near-exact match required — wrong SQL for a similar query is dangerous
FAILED_SIMILARITY_THRESHOLD = 0.99  # near-exact match required — suggestions from a different query may not apply


def _get_embeddings() -> OllamaEmbeddings:
    from src.connections import get_embedding_model
    return OllamaEmbeddings(
        model=get_embedding_model(),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )


class SemanticCache:
    def __init__(self):
        self._embeddings = _get_embeddings()
        self._store: FAISS | None = None
        self._load()

    def _load(self):
        if os.path.exists(CACHE_DIR):
            try:
                self._store = FAISS.load_local(
                    CACHE_DIR,
                    self._embeddings,
                    allow_dangerous_deserialization=True,
                )
            except Exception:
                self._store = None

    def lookup(self, raw_sql: str) -> str | None:
        if not self._store:
            return None
        results = self._store.similarity_search_with_relevance_scores(raw_sql, k=1)
        if results and results[0][1] >= SUCCESS_SIMILARITY_THRESHOLD:
            doc = results[0][0]
            if not doc.metadata.get("failed", False):
                return doc.metadata.get("optimized_sql")
        return None

    def lookup_failed(self, raw_sql: str) -> list[str] | None:
        if not self._store:
            return None
        results = self._store.similarity_search_with_relevance_scores(raw_sql, k=1)
        if results and results[0][1] >= FAILED_SIMILARITY_THRESHOLD:
            doc = results[0][0]
            if doc.metadata.get("failed", False):
                return doc.metadata.get("suggestions") or None
        return None

    def store(self, raw_sql: str, optimized_sql: str):
        doc = Document(page_content=raw_sql, metadata={"optimized_sql": optimized_sql, "failed": False})
        if self._store:
            self._store.add_documents([doc])
        else:
            self._store = FAISS.from_documents([doc], self._embeddings)
        os.makedirs(CACHE_DIR, exist_ok=True)
        self._store.save_local(CACHE_DIR)

    def store_failed(self, raw_sql: str, suggestions: list[str]):
        doc = Document(page_content=raw_sql, metadata={"optimized_sql": None, "suggestions": suggestions, "failed": True})
        if self._store:
            self._store.add_documents([doc])
        else:
            self._store = FAISS.from_documents([doc], self._embeddings)
        os.makedirs(CACHE_DIR, exist_ok=True)
        self._store.save_local(CACHE_DIR)
