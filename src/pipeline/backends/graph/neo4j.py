from src.pipeline.backends.graph.graph import GraphBackend


class Neo4jBackend(GraphBackend):
    """Neo4j backend — stub v1.0. Full implementation pending."""

    def sqlglot_dialect(self) -> str:
        return ""

    def syntax_hint(self) -> str:
        return (
            "Syntax rules for Cypher (Neo4j):\n"
            "- Structure: MATCH (n:Label)-[:REL]->(m:Label) WHERE condition RETURN n\n"
            "- Use MATCH for patterns, WHERE for filters, RETURN for output\n"
            "- No semicolon at the end"
        )

    def explain(self, query: str) -> dict:
        return {"type": "neo4j", "plan": {}, "execution_time_ms": None, "error": "Neo4j PROFILE not yet implemented"}

    def explain_analyze(self, query: str) -> dict:
        return self.explain(query)

    def hardware_hints(self) -> dict:
        return {}

    def get_total_cost(self, explain_result: dict) -> float | None:
        return None

    def extract_table_estimates(self, explain_result: dict) -> dict[str, int]:
        return {}

    def sample_hash(self, query: str) -> tuple[str | None, str | None]:
        return None, "Neo4j sampling not yet implemented"

    def get_partial_index_predicates(self, table_name: str) -> dict[str, str]:
        return {}
