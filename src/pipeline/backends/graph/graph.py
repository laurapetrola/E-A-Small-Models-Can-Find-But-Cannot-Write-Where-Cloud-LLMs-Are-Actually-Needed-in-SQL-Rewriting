from abc import ABC, abstractmethod


class GraphBackend(ABC):
    """Common graph database backend — subclass and implement the abstract methods for each graph DBMS."""

    # ── Abstract — must be implemented per graph DBMS ─────────────────────────

    @abstractmethod
    def sqlglot_dialect(self) -> str: ...

    @abstractmethod
    def syntax_hint(self) -> str: ...

    @abstractmethod
    def explain(self, query: str) -> dict: ...

    @abstractmethod
    def explain_analyze(self, query: str) -> dict: ...

    @abstractmethod
    def hardware_hints(self) -> dict: ...

    @abstractmethod
    def get_total_cost(self, explain_result: dict) -> float | None: ...

    @abstractmethod
    def extract_table_estimates(self, explain_result: dict) -> dict[str, int]: ...

    @abstractmethod
    def sample_hash(self, query: str) -> tuple[str | None, str | None]: ...

    @abstractmethod
    def get_partial_index_predicates(self, table_name: str) -> dict[str, str]: ...

    # ── Concrete defaults — override per DBMS when graph-specific logic exists ──

    def validate_syntax(self, query: str) -> tuple[bool, str]:
        return True, ""

    def extract_references(self, query: str) -> set[str]:
        return set()

    def parse_query(self, query: str) -> dict | None:
        return None

    def parse_decisions(self, llm_response: str, components: dict) -> dict | None:
        return None

    def reconstruct_query(self, components: dict, decisions: dict) -> str | None:
        return None

    def build_optimization_prompt(
        self,
        components: dict,
        explain: dict,
        hw_hints: dict,
        schema_context: dict | None = None,
        errors: list[str] | None = None,
        retry_hint: str | None = None,
        table_estimates: dict | None = None,
    ) -> str:
        return ""

    def build_fallback_prompt(self, raw_query: str, explain: dict, errors: list[str], hw_hints: dict, retry_hint: str | None) -> str:
        return ""

    def extract_query(self, text: str) -> str:
        return text.strip()

    def check_structural_integrity(self, raw_query: str, optimized_query: str) -> list[str]:
        return []

    def causal_regression_hint(self, optimized_query: str) -> str | None:
        return None

    def extract_schema(self) -> tuple[list[dict], list[dict]]:
        return [], []

    def ping_latency(self) -> float:
        return 0.0

    def db_version(self) -> str:
        return ""

    def schema_fingerprint(self) -> str | None:
        return None  # graph backends don't track index state

    def is_timeout_error(self, error_message: str) -> bool:
        return "timeout" in error_message.lower()
