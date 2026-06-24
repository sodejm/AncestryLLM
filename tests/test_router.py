import pytest
from pathlib import Path
from unittest.mock import Mock, patch

import tools.sql_router as sql_router


@pytest.fixture(autouse=True)
def clear_agent_cache():
    """Ensure the in-memory agent cache is empty before every test."""
    sql_router._agent_cache.clear()
    yield
    sql_router._agent_cache.clear()


def test_router_lists_available_trees_when_tree_name_is_missing():
    with patch("tools.sql_router.os.path.exists", return_value=True), patch(
        "tools.sql_router.os.listdir", return_value=["alpha.rmtree", "beta.rmtree", "ignore.txt"]
    ):
        result = sql_router.route_sql_query("select 1")

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
                return {"output": "answer"}

        return FakeAgent()

    with patch("tools.sql_router.os.path.exists", return_value=True), patch(
        "tools.sql_router.os.path.getmtime", return_value=1000.0
    ), patch(
        "tools.sql_router.SQLDatabase.from_uri", side_effect=fake_sql_database_from_uri
    ), patch("tools.sql_router._build_sql_agent", side_effect=fake_build_sql_agent):
        result = sql_router.route_sql_query("select * from people", tree_name="alpha")

    assert result == "answer"
    assert calls["uri"] == f"sqlite+pysqlite:///{fake_tree_path.as_posix()}?mode=ro&uri=true"
    assert calls["payload"] == {"input": "select * from people"}
    assert calls["engine_args"]["pool_size"] == 5
    assert calls["engine_args"]["connect_args"] == {"check_same_thread": False}


def test_router_gracefully_handles_missing_tree_name():
    with patch("tools.sql_router.os.path.exists", return_value=False), patch(
        "tools.sql_router.os.listdir", return_value=[]
    ):
        result = sql_router.route_sql_query("select 1", tree_name="missing")

    assert result == "Tree file not found: missing.rmtree"


def test_router_reuses_cached_agent_for_repeated_queries():
    """SQLDatabase and agent must be built only once for consecutive same-tree queries."""
    build_count = {"db": 0, "agent": 0}

    def fake_sql_database_from_uri(uri, engine_args=None):
        build_count["db"] += 1
        return Mock(name="database")

    def fake_build_sql_agent(database):
        build_count["agent"] += 1

        class FakeAgent:
            def invoke(self, payload):
                return {"output": "cached"}

        return FakeAgent()

    with patch("tools.sql_router.os.path.exists", return_value=True), patch(
        "tools.sql_router.os.path.getmtime", return_value=1000.0
    ), patch(
        "tools.sql_router.SQLDatabase.from_uri", side_effect=fake_sql_database_from_uri
    ), patch("tools.sql_router._build_sql_agent", side_effect=fake_build_sql_agent):
        result1 = sql_router.route_sql_query("first query", tree_name="alpha")
        result2 = sql_router.route_sql_query("second query", tree_name="alpha")

    assert result1 == "cached"
    assert result2 == "cached"
    # Both calls share the same cached objects — build functions invoked exactly once.
    assert build_count["db"] == 1
    assert build_count["agent"] == 1


def test_router_invalidates_cache_when_mtime_changes():
    """A stale cache entry (mtime changed) must trigger a full rebuild."""
    build_count = {"db": 0, "agent": 0}
    mtime_value = [1000.0]

    def fake_sql_database_from_uri(uri, engine_args=None):
        build_count["db"] += 1
        return Mock(name="database")

    def fake_build_sql_agent(database):
        build_count["agent"] += 1

        class FakeAgent:
            def invoke(self, payload):
                return {"output": "ok"}

        return FakeAgent()

    def fake_getmtime(path):
        return mtime_value[0]

    with patch("tools.sql_router.os.path.exists", return_value=True), patch(
        "tools.sql_router.os.path.getmtime", side_effect=fake_getmtime
    ), patch(
        "tools.sql_router.SQLDatabase.from_uri", side_effect=fake_sql_database_from_uri
    ), patch("tools.sql_router._build_sql_agent", side_effect=fake_build_sql_agent):
        sql_router.route_sql_query("first query", tree_name="alpha")
        # Simulate file modification.
        mtime_value[0] = 2000.0
        sql_router.route_sql_query("second query", tree_name="alpha")

    # The mtime change must have triggered a second build.
    assert build_count["db"] == 2
    assert build_count["agent"] == 2
