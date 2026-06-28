import os
from pathlib import Path
from unittest.mock import Mock, patch

import tools.sql_router as sql_router


def test_router_lists_available_trees_when_tree_name_is_missing():
    with patch("tools.sql_router.os.path.exists", return_value=True), patch(
        "tools.sql_router.os.listdir", return_value=["alpha.rmtree", "beta.rmtree", "ignore.txt"]
    ):
        result = sql_router.dynamic_sqlite_router("select 1")

    assert result == "Available .rmtree files:\n- alpha.rmtree\n- beta.rmtree"


def test_router_builds_read_only_database_for_requested_tree():
    fake_tree_path = Path("/app/backend/data/family_trees/alpha.rmtree")
    calls = {}

    def fake_sql_database_from_uri(uri, engine_args=None):
        calls["uri"] = uri
        calls["engine_args"] = engine_args
        return Mock(name="database")

    def fake_build_sql_agent(database):
        calls["database"] = database

        class FakeAgent:
            def invoke(self, payload):
                calls["payload"] = payload
                return {"messages": [{"role": "assistant", "content": "answer"}]}

        return FakeAgent()

    with patch("tools.sql_router.os.path.exists", return_value=True), patch(
        "tools.sql_router.SQLDatabase.from_uri", side_effect=fake_sql_database_from_uri
    ), patch("tools.sql_router._build_sql_agent", side_effect=fake_build_sql_agent):
        result = sql_router.dynamic_sqlite_router("select * from people", tree_name="alpha")

    assert result == "answer"
    assert calls["uri"] == f"sqlite+pysqlite:///{fake_tree_path.as_posix()}?mode=ro&uri=true"
    assert calls["payload"] == {
        "messages": [{"role": "user", "content": "select * from people"}]
    }
    assert calls["engine_args"]["pool_size"] == 5
    assert calls["engine_args"]["connect_args"] == {"check_same_thread": False}


def test_router_gracefully_handles_missing_tree_name():
    with patch("tools.sql_router.os.path.exists", return_value=False), patch(
        "tools.sql_router.os.listdir", return_value=[]
    ):
        result = sql_router.dynamic_sqlite_router("select 1", tree_name="missing")

    assert result == "Tree file not found: missing.rmtree"


def test_router_uses_env_configured_family_trees_dir():
    custom_dir = "/mounted/rootsmagic"

    with patch.dict(os.environ, {"FAMILY_TREES_DIR": custom_dir}, clear=False), patch(
        "tools.sql_router.os.path.exists", return_value=True
    ), patch("tools.sql_router.os.listdir", return_value=["alpha.rmtree"]):
        result = sql_router.dynamic_sqlite_router("select 1")
        resolved_path = sql_router._resolve_tree_path("alpha")

    assert result == "Available .rmtree files:\n- alpha.rmtree"
    assert resolved_path == Path(f"{custom_dir}/alpha.rmtree")
