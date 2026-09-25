import os
from typing import Any
from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_community.utilities import SQLDatabase
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

load_dotenv()

# --- LLM ---

FAMILIES = {
    "deepseek": os.getenv("DEEPSEEK_MODEL", "deepseek-r1:8b"),
    # deepseek-r1:8b is the R1-0528 distill on a Qwen3-8B base (same architecture as
    # qwen-reasoning). The -llama-distill variant is R1 distilled on a Llama-3.1-8B base —
    # used as the cross-architecture reasoning datapoint (different base, same R1 reasoning).
    "deepseek-llama": os.getenv("DEEPSEEK_LLAMA_MODEL", "deepseek-r1:8b-llama-distill-q4_K_M"),
    "deepseek-coder": os.getenv("DEEPSEEK_CODER_MODEL", "deepseek-coder:6.7b"),
    "qwen": os.getenv("QWEN_MODEL", "qwen2.5:7b"),
    "qwen-reasoning": os.getenv("QWEN_REASONING_MODEL", "qwen3:8b"),
    "phi": os.getenv("PHI_MODEL", "phi4-mini-reasoning"),
    "granite": os.getenv("GRANITE_MODEL", "granite3.2:8b"),
    # Non-reasoning instruct baselines (the "does reasoning help?" contrast — paper §5):
    "llama": os.getenv("LLAMA_MODEL", "llama3.1:8b"),       # Llama base, NON-reasoning (vs r1-distill-llama)
    "mistral": os.getenv("MISTRAL_MODEL", "mistral:7b-instruct"),    # Mistral base, NON-reasoning
}


def get_embedding_model() -> str:
    if os.getenv("EMBEDDING_MODEL"):
        return os.getenv("EMBEDDING_MODEL")
    active = os.getenv("MODEL_FAMILY", "deepseek")
    return FAMILIES.get(active, FAMILIES["deepseek"])


def get_llm(family: str | None = None, reasoning: bool | None = None) -> ChatOllama:
    active = family or os.getenv("MODEL_FAMILY", "deepseek")
    if active not in FAMILIES:
        raise ValueError(f"Unknown model family '{active}'. Choose from: {list(FAMILIES)}")
    kwargs: dict[str, Any] = dict(
        model=FAMILIES[active],
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        num_ctx=16384,
        temperature=0,
        # Fixed seed for reproducibility. temperature=0 is greedy, so the seed should not
        # change the argmax — this isolates how much of the run-to-run variance is the sampler
        # (seed-dependent) vs inherent (GPU/batching non-determinism + long-CoT amplification).
        # Configurable so best-of-N (future) can vary it per sample if we want diverse rewrites.
        seed=int(os.getenv("OLLAMA_SEED", "42")),
    )
    # reasoning=False disables the model's chain-of-thought (qwen3 / deepseek-r1). Used for the
    # post-hoc LABELER: it is a classification task (name the technique), not reasoning, so the long
    # CoT was pure cost (~31 min on a q9 run) AND its <think> block broke the JSON parse → empty
    # labels (the FIND was lost). Safe on non-thinking models (Ollama treats think:false as the default).
    if reasoning is not None:
        kwargs["reasoning"] = reasoning
    return ChatOllama(**kwargs)


# --- Database (agnostic layer) ---

def get_engine(uri: str | None = None) -> Engine:
    # `uri` lets a caller target a DB other than the primary DB_URI — e.g. the smaller-scale
    # verification instance (VERIFY_DB_URI) used to check S=1 when the full-scale original times out.
    uri = uri or os.getenv("DB_URI")
    if not uri:
        raise ValueError("DB_URI is required")
    # ⛔⛔ POOL COM RECICLAGEM (04/09) — sem isto, uma conexão OCIOSA segura metadata lock PARA SEMPRE.
    #
    #   O SINTOMA, que apareceu TRÊS vezes em 04/09 (2× no tpcds-mysql, 1× no imdb-mysql): um
    #   `DROP INDEX _athena_sim_...` fica em "Waiting for table metadata lock" por HORAS, e tudo atrás
    #   dele — ANALYZE, EXPLAIN, os SELECT de baseline — entra na fila. A célula não trava com erro:
    #   ela vai acumulando **timeout de baseline**, que não conta como run. No IMDb/MySQL isso custou
    #   2h15 sem um único run novo, com 4 timeouts que PARECEM limite do modelo e são um lock.
    #
    #   A CAUSA: `create_engine(uri)` sem argumentos usa o QueuePool com `pool_recycle=-1` — conexão
    #   devolvida ao pool vive indefinidamente. Se ela voltou com uma transação de leitura aberta
    #   (InnoDB em REPEATABLE READ abre transação no primeiro SELECT), o metadata lock fica retido
    #   enquanto ela dorme no pool. `Sleep` de 6.804 s segurando `movie_info` foi o caso medido.
    #
    #   ⚠️ `pool_recycle` é TETO, não espera: conexão saudável e em uso não é afetada. O que ele faz é
    #      garantir que nenhuma conexão ociosa sobreviva mais que o teto — o lock cai junto com ela.
    #   ⚠️ `pool_pre_ping` cobre o outro lado: depois de um reboot de contêiner, conexões mortas no
    #      pool eram entregues e falhavam na primeira query.
    return create_engine(
        uri,
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE_S", "600")),
        pool_pre_ping=True,
    )


def _build_sql() -> SQLDatabase:
    uri = os.getenv("DB_URI")
    if not uri:
        raise ValueError("DB_URI is required for DB_TYPE=sql")
    return SQLDatabase.from_uri(uri)


def _build_neo4j() -> Any:
    # Requires: pip install langchain-neo4j
    from langchain_neo4j import Neo4jGraph  # type: ignore[import]
    return Neo4jGraph(
        url=os.getenv("DB_URI"),
        username=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


# Graph backends need a dedicated driver — all others use SQLAlchemy (get_engine).
# Add new graph backends here; new relational backends work automatically.
_GRAPH_DB_TYPES: frozenset[str] = frozenset({"neo4j"})

# URI scheme → db_type for auto-detection (mirrors backends/__init__._URI_PREFIXES)
_URI_PREFIXES: list[tuple[str, str]] = [
    ("postgresql", "postgres"),
    ("postgres",   "postgres"),
    ("mysql",      "mysql"),
    ("neo4j",      "neo4j"),
]


def _resolve_db_type() -> str:
    db_type = os.getenv("DB_TYPE", "").strip().lower()
    if db_type:
        return db_type
    uri = os.getenv("DB_URI", "")
    for prefix, detected in _URI_PREFIXES:
        if uri.startswith(prefix):
            return detected
    return "postgres"


def get_db() -> Any:
    db_type = _resolve_db_type()
    if db_type in _GRAPH_DB_TYPES:
        return _build_neo4j()
    return _build_sql()
