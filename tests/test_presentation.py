from __future__ import annotations

from io import StringIO

import pytest

from ancestryllm.application.dto import ErrorEnvelope
from ancestryllm.application.results import ErrorResult, WarningResult
from ancestryllm.console.presentation import PresentationAdapter, to_plain
from ancestryllm.core.errors import AncestryError


def test_adapter_renders_serializable_dtos_and_json() -> None:
    output = StringIO()
    adapter = PresentationAdapter.for_file(output)

    adapter.render({"path": "safe", "items": [1, 2]}, json_output=True)

    assert '"items": [' in output.getvalue()
    assert '"path": "safe"' in output.getvalue()


def test_adapter_renders_stable_errors_without_rich_markup() -> None:
    output = StringIO()
    adapter = PresentationAdapter.for_file(output)

    adapter.render_error(AncestryError("SAFE_CODE", "A safe message", "Do the safe thing."))

    assert output.getvalue() == "[SAFE_CODE] A safe message\nHow to fix: Do the safe thing.\n"


def test_adapter_renders_warning_and_error_result_semantics_not_serialized_shapes() -> None:
    output = StringIO()
    adapter = PresentationAdapter.for_file(output)

    adapter.render(WarningResult("RESULT_PARTIAL", "Some records were skipped."))
    adapter.render(
        ErrorResult(
            ErrorEnvelope(
                code="RESULT_INVALID",
                message="The result is invalid.",
                remediation="Retry with valid input.",
                correlation_ref=None,
            )
        )
    )

    assert output.getvalue() == (
        "[RESULT_PARTIAL] Some records were skipped.\n"
        "[RESULT_INVALID] The result is invalid.\n"
        "How to fix: Retry with valid input.\n"
    )


def test_render_error_routes_through_the_declared_error_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    adapter = PresentationAdapter.for_file(output)
    rendered: list[object] = []

    def record(value: object, *, json_output: bool = False) -> None:
        assert json_output is False
        rendered.append(value)

    monkeypatch.setattr(adapter, "render", record)

    adapter.render_error(AncestryError("SAFE_CODE", "A safe message"))

    assert len(rendered) == 1
    assert isinstance(rendered[0], ErrorResult)


def test_to_plain_converts_paths_without_rendering_them() -> None:
    from pathlib import Path

    assert to_plain({"output": Path("report.json")}) == {"output": "report.json"}
