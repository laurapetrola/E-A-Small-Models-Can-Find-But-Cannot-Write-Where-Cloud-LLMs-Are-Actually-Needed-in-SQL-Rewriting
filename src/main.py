import logging
import os
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(name)s — %(message)s")
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from src.connections import FAMILIES, get_db, get_llm
from src.routers.input_router import router as input_router
from src.pipeline.nodes.schema_analyst import invalidate_cache

load_dotenv()

app = FastAPI(title="Athena", version="0.1.0")
app.include_router(input_router)


class ChatRequest(BaseModel):
    message: str
    model_family: str | None = None  # overrides MODEL_FAMILY env var if provided


class ChatResponse(BaseModel):
    model_family: str
    model: str
    response: str


@app.get("/health")
def health():
    results = {"llm": "ok", "db": "ok"}

    try:
        import httpx
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        r = httpx.get(f"{base_url}/api/tags", timeout=3)
        if r.status_code != 200:
            results["llm"] = f"Ollama returned {r.status_code}"
    except Exception as e:
        results["llm"] = str(e)

    try:
        db = get_db()
        db.get_usable_table_names()
    except Exception as e:
        results["db"] = str(e)

    status = "healthy" if all(v == "ok" for v in results.values()) else "degraded"
    return {"status": status, **results}


@app.get("/models")
def list_models():
    active = os.getenv("MODEL_FAMILY", "deepseek")
    return {
        "active_family": active,
        "families": {name: model for name, model in FAMILIES.items()},
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    family = req.model_family or os.getenv("MODEL_FAMILY", "deepseek")
    if family not in FAMILIES:
        raise HTTPException(status_code=400, detail=f"Unknown family '{family}'. Options: {list(FAMILIES)}")
    try:
        llm = get_llm(family)
        result = llm.invoke(req.message)
        return ChatResponse(
            model_family=family,
            model=FAMILIES[family],
            response=result.content,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/pipeline/schema/invalidate")
def invalidate_schema_cache():
    """Force schema_analyst to recompute on the next request (use when DB schema changes)."""
    invalidate_cache()
    return {"message": "Schema cache invalidated. Will recompute on next pipeline call."}


@app.get("/db/tables")
def db_tables():
    try:
        db = get_db()
        return {"tables": db.get_usable_table_names()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/db/connect")
def db_connect():
    try:
        db = get_db()
        tables = db.get_usable_table_names()

        llm = get_llm()
        prompt = (
            f"You have successfully connected to the database. "
            f"The following tables are available: {tables}. "
            f"In one sentence, confirm the connection and list the tables."
        )
        result = llm.invoke(prompt)

        return {
            "connected": True,
            "tables": tables,
            "llm_says": result.content,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
