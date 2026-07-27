"""Ordered, failure-resistant application resource cleanup."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ancestryllm.core.context import AppContext
from ancestryllm.llm.service import LLMService


class _Closer:
    def __init__(self, name: str, order: list[str], *, failure: Exception | None = None) -> None:
        self.name = name
        self.order = order
        self.failure = failure

    def close(self) -> None:
        self.order.append(self.name)
        if self.failure is not None:
            raise self.failure


def test_llm_close_attempts_every_resource_and_propagates_first_failure() -> None:
    order: list[str] = []
    llm = object.__new__(LLMService)
    llm.execution = _Closer(
        "execution",
        order,
        failure=RuntimeError("fictional execution close failure"),
    )
    llm.cache = _Closer("cache", order)
    llm.registry = _Closer(
        "providers",
        order,
        failure=RuntimeError("fictional provider close failure"),
    )

    with pytest.raises(RuntimeError, match="execution close failure"):
        llm.close()

    assert order == ["execution", "cache", "providers"]


def test_context_closes_database_even_when_provider_cleanup_fails() -> None:
    order: list[str] = []
    context: Any = SimpleNamespace(
        llm=_Closer(
            "llm",
            order,
            failure=RuntimeError("fictional provider cleanup failure"),
        ),
        database=_Closer("database", order),
    )

    with pytest.raises(RuntimeError, match="provider cleanup failure"):
        AppContext.close(context)

    assert order == ["llm", "database"]
