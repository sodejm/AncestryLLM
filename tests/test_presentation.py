from __future__ import annotations

from io import StringIO

import pytest

from ancestryllm.application.dto import ArtifactRef, ArtifactStatus, ErrorEnvelope
from ancestryllm.application.results import (
    ErrorResult,
    FileArtifactResult,
    StructuredResult,
    WarningResult,
)
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


def test_to_plain_rejects_paths_instead_of_rendering_host_details() -> None:
    from pathlib import Path

    with pytest.raises(TypeError, match="Path"):
        to_plain({"output": Path("report.json")})


def test_adapter_explicitly_routes_structured_and_file_artifact_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    adapter = PresentationAdapter.for_file(output)
    routed: list[tuple[str, object]] = []
    artifact = ArtifactRef(
        artifact_id=f"art_{'a' * 32}",
        media_type="text/plain",
        artifact_type="report",
        size_bytes=12,
        status=ArtifactStatus.READY,
        sha256="b" * 64,
    )

    monkeypatch.setattr(
        adapter,
        "_render_structured",
        lambda result: routed.append(("structured", result)),
        raising=False,
    )
    monkeypatch.setattr(
        adapter,
        "_render_file_artifact",
        lambda result: routed.append(("file_artifact", result)),
        raising=False,
    )
    monkeypatch.setattr(
        adapter,
        "_render_plain",
        lambda _plain: pytest.fail("declared results must not use shape-based fallback"),
    )

    structured = StructuredResult({"count": 2})
    file_artifact = FileArtifactResult(artifact)
    adapter.render(structured)
    adapter.render(file_artifact)

    assert routed == [
        ("structured", structured),
        ("file_artifact", file_artifact),
    ]
