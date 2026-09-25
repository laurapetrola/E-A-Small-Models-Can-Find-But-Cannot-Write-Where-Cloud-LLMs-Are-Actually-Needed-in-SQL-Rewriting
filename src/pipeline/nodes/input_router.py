from src.pipeline.state import PipelineState


def input_router(state: PipelineState) -> PipelineState:
    """Receives raw SQL and forwards it to the next node without any preprocessing."""
    return {"raw_sql": state["raw_sql"], "status": "routed"}
