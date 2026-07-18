from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Optional

from langchain.agents import create_agent

# `SQLDatabase`/`SQLDatabaseToolkit` still live in `langchain-community`, which
# emits a package-level sunset DeprecationWarning at import time. No standalone
# replacement package exists yet (see
# https://github.com/langchain-ai/langchain-community/issues/674), so we suppress
# only that specific notice here. TODO: drop this filter and migrate once an
# official standalone SQL utilities package ships.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="`langchain-community` is being sunset",
        category=DeprecationWarning,
    )
    from langchain_community.agent_toolkits import SQLDatabaseToolkit
    from langchain_community.utilities import SQLDatabase

DEFAULT_FAMILY_TREES_DIR = Path("/app/backend/data/family_trees")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
# Bound the local model context window so it fits within a 24GB VRAM budget
# instead of overflowing into host RAM.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
# Cap the rows the SQL agent may return to avoid memory-exhausting dumps from a
# 15,000-person database.
SQL_AGENT_TOP_K = int(os.getenv("SQL_AGENT_TOP_K", "20"))

# Concise prompt forces explicit column lists and LIMIT clauses to keep query
# result sets small and predictable.
SQL_AGENT_PREFIX = (
    "You are a careful SQLite genealogy analyst. "
    "Select explicit columns only; never use SELECT *. "
    f"Always append a LIMIT clause of at most {SQL_AGENT_TOP_K} rows to every query. "
    "Read only the columns required to answer the question."
)

# In-memory cache: maps (tree_path_str, model, base_url, num_ctx, top_k) -> (mtime, database, agent).
# Entries are invalidated when the underlying .rmtree file changes (detected via mtime) or when
# the active LLM configuration changes.  Never persisted to disk.
_CacheKey = tuple[str, str, str, int, int]
_CacheEntry = tuple[float, SQLDatabase, object]
_agent_cache: dict[_CacheKey, _CacheEntry] = {}
_cache_lock = threading.Lock()


def _get_family_trees_dir() -> Path:
    return Path(os.getenv("FAMILY_TREES_DIR", str(DEFAULT_FAMILY_TREES_DIR)))


def list_family_tree_files() -> str:
    """Return a formatted list of available ``.rmtree`` files, or a not-found message."""
    if not os.path.exists(FAMILY_TREES_DIR):
        return f"No .rmtree files found in {FAMILY_TREES_DIR}."

    tree_files = sorted(
        file_name for file_name in os.listdir(family_trees_dir) if file_name.endswith(".rmtree")
    )
    if not tree_files:
        return f"No .rmtree files found in {family_trees_dir}."

    return "Available .rmtree files:\n" + "\n".join(f"- {file_name}" for file_name in tree_files)


def _resolve_tree_path(tree_name: str) -> Path | None:
    """Resolve *tree_name* to an absolute path inside the family-trees directory.

    Returns ``None`` when the resolved path escapes the allowed directory (path
    traversal prevention).
    """
    candidate = FAMILY_TREES_DIR / tree_name
    if candidate.suffix != ".rmtree":
        candidate = candidate.with_suffix(".rmtree")

    candidate_path = candidate.resolve(strict=False)
    candidate_path_str = os.fspath(candidate_path)
    if os.path.commonpath([str(base_family_trees_dir), candidate_path_str]) != str(
        base_family_trees_dir
    ):
        return None
    return candidate_path


def _build_read_only_sqlite_uri(tree_path: Path) -> str:
    """Build a SQLAlchemy URI that opens *tree_path* in read-only mode."""
    return f"sqlite+pysqlite:///{tree_path.as_posix()}?mode=ro&uri=true"


def _build_engine_args() -> dict[str, object]:
    """Return SQLAlchemy engine kwargs for pooled, thread-safe SQLite connections.

    Thread-safe pooled connections keep the SQLite footprint low and recycle
    idle handles instead of leaking them per request.
    """
    return {
        "pool_size": 5,
        "max_overflow": 10,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
        "connect_args": {"check_same_thread": False},
    }


def _build_sql_agent(database: SQLDatabase):
    """Construct a LangChain SQL agent backed by a local Ollama LLM.

    The agent is configured with the shared prefix and row-count cap defined
    at module level to constrain query scope and memory usage.
    """
    from langchain_ollama import ChatOllama

    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        num_ctx=OLLAMA_NUM_CTX,
    )
    toolkit = SQLDatabaseToolkit(db=database, llm=llm)
    return create_agent(
        llm,
        toolkit.get_tools(),
        system_prompt=SQL_AGENT_PREFIX,
    )


def _get_cache_key(tree_path: Path) -> _CacheKey:
    """Build a cache key from the resolved tree path and active LLM configuration.

    Including the configuration variables means any environment-variable change
    automatically produces a cache miss, so stale agents are never reused.
    """
    return (str(tree_path), OLLAMA_MODEL, OLLAMA_BASE_URL, OLLAMA_NUM_CTX, SQL_AGENT_TOP_K)


def _get_or_build_agent(tree_path: Path) -> _CacheEntry:
    """Return a cached (database, agent) pair, rebuilding when the file or config changes.

    Cache hits avoid the cost of re-initialising SQLAlchemy and the LLM client
    for repeated queries against the same tree.  Stale entries are detected via
    the file's mtime so any write to the underlying ``.rmtree`` file causes a
    transparent rebuild on the next query.
    """
    config_key = _get_cache_key(tree_path)
    current_mtime = os.path.getmtime(tree_path)

    with _cache_lock:
        cached = _agent_cache.get(config_key)
        if cached is not None:
            cached_mtime, database, agent = cached
            if cached_mtime == current_mtime:
                return database, agent

        # Cache miss or stale entry — build fresh objects.
        database = SQLDatabase.from_uri(
            _build_read_only_sqlite_uri(tree_path),
            engine_args=_build_engine_args(),
        )
        agent = _build_sql_agent(database)
        _agent_cache[config_key] = (current_mtime, database, agent)
        return database, agent


def route_sql_query(query: str, tree_name: Optional[str] = None) -> str:
    """Route *query* to the appropriate family-tree SQLite database.

    When *tree_name* is omitted, returns a listing of available ``.rmtree``
    files instead of executing the query.  Raises nothing on ordinary errors;
    human-readable messages are returned as strings so callers can relay them
    directly to end-users.
    """
    if not tree_name:
        return list_family_tree_files()

    tree_path = _resolve_tree_path(tree_name)
    if tree_path is None:
        return f"Tree file not found: {Path(tree_name).with_suffix('.rmtree').name}"

    if not os.path.exists(tree_path):
        return f"Tree file not found: {tree_path.name}"

    _database, agent = _get_or_build_agent(tree_path)  # database owned by cache
    result = agent.invoke({"input": query})
    if isinstance(result, dict) and "output" in result:
        return str(result["output"])
    return str(result)


if __name__ == "__main__":
    print(list_family_tree_files())
