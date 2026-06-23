from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from langchain_community.agent_toolkits import create_sql_agent
from langchain_community.utilities import SQLDatabase

FAMILY_TREES_DIR = Path("/app/backend/data/family_trees")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
# Bound the local model context window so it fits within a 24GB VRAM budget
# instead of overflowing into host RAM.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
# Cap the rows the SQL agent may return to avoid memory-exhausting dumps from a
# 15,000-person database.
SQL_AGENT_TOP_K = int(os.getenv("SQL_AGENT_TOP_K", "20"))
BASE_FAMILY_TREES_DIR = FAMILY_TREES_DIR.resolve(strict=False)

# Concise prompt forces explicit column lists and LIMIT clauses to keep query
# result sets small and predictable.
SQL_AGENT_PREFIX = (
    "You are a careful SQLite genealogy analyst. "
    "Select explicit columns only; never use SELECT *. "
    f"Always append a LIMIT clause of at most {SQL_AGENT_TOP_K} rows to every query. "
    "Read only the columns required to answer the question."
)


def list_family_tree_files() -> str:
    if not os.path.exists(FAMILY_TREES_DIR):
        return f"No .rmtree files found in {FAMILY_TREES_DIR}."

    tree_files = sorted(
        file_name for file_name in os.listdir(FAMILY_TREES_DIR) if file_name.endswith(".rmtree")
    )
    if not tree_files:
        return f"No .rmtree files found in {FAMILY_TREES_DIR}."

    return "Available .rmtree files:\n" + "\n".join(f"- {file_name}" for file_name in tree_files)


def _resolve_tree_path(tree_name: str) -> Path | None:
    candidate = FAMILY_TREES_DIR / tree_name
    if candidate.suffix != ".rmtree":
        candidate = candidate.with_suffix(".rmtree")

    candidate_path = candidate.resolve(strict=False)
    candidate_path_str = os.fspath(candidate_path)
    if os.path.commonpath([str(BASE_FAMILY_TREES_DIR), candidate_path_str]) != str(BASE_FAMILY_TREES_DIR):
        return None
    return candidate_path


def _build_read_only_sqlite_uri(tree_path: Path) -> str:
    return f"sqlite+pysqlite:///{tree_path.as_posix()}?mode=ro&uri=true"


def _build_engine_args() -> dict:
    # Thread-safe pooled connections keep the SQLite footprint low and recycle
    # idle handles instead of leaking them per request.
    return {
        "pool_size": 5,
        "max_overflow": 10,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
        "connect_args": {"check_same_thread": False},
    }


def _build_sql_agent(database: SQLDatabase):
    from langchain_ollama import ChatOllama

    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        num_ctx=OLLAMA_NUM_CTX,
    )
    return create_sql_agent(
        llm=llm,
        db=database,
        verbose=False,
        prefix=SQL_AGENT_PREFIX,
        top_k=SQL_AGENT_TOP_K,
    )


def dynamic_sqlite_router(query: str, tree_name: Optional[str] = None) -> str:
    if not tree_name:
        return list_family_tree_files()

    tree_path = _resolve_tree_path(tree_name)
    if tree_path is None:
        return f"Tree file not found: {Path(tree_name).with_suffix('.rmtree').name}"

    if not os.path.exists(tree_path):
        return f"Tree file not found: {tree_path.name}"

    database = SQLDatabase.from_uri(
        _build_read_only_sqlite_uri(tree_path),
        engine_args=_build_engine_args(),
    )

    agent = _build_sql_agent(database)
    result = agent.invoke({"input": query})
    if isinstance(result, dict) and "output" in result:
        return str(result["output"])
    return str(result)


def route_sql_query(query: str, tree_name: Optional[str] = None) -> str:
    return dynamic_sqlite_router(query=query, tree_name=tree_name)


if __name__ == "__main__":
    print(list_family_tree_files())
