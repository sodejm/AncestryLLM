"""Shared pytest configuration for repository-safe subprocess tests."""

from __future__ import annotations

import pytest

# Git exports repository-local variables while invoking hooks. Leaving any of
# these in the pytest process can redirect a nested ``git -C`` command back to
# the repository that launched the hook instead of the temporary test fixture.
_GIT_LOCAL_ENVIRONMENT = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_DIR",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
)


@pytest.fixture(autouse=True)
def isolate_tests_from_invoking_git_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep test subprocesses independent of the Git hook that launched pytest."""

    for variable in _GIT_LOCAL_ENVIRONMENT:
        monkeypatch.delenv(variable, raising=False)
