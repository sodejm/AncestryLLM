from __future__ import annotations

import json
from io import StringIO

import pytest
from rich.console import Console

from ancestryllm.application.dto import ArtifactRef, ArtifactStatus, ErrorEnvelope
from ancestryllm.application.results import (
    ErrorResult,
    FileArtifactResult,
    MarkdownResult,
    StructuredResult,
    SuccessResult,
    TableResult,
    WarningResult,
)
from ancestryllm.console.presentation import PresentationAdapter, to_plain
from ancestryllm.core.errors import AncestryError


def _captured_console() -> Console:
    """Return deterministic settings for captured Rich output."""

    return Console(
        file=StringIO(),
        force_terminal=False,
        color_system=None,
        highlight=False,
        width=120,
    )


def _artifact() -> ArtifactRef:
    return ArtifactRef(
        artifact_id=f"art_{'a' * 32}",
        media_type="text/plain",
        artifact_type="report",
        size_bytes=12,
        status=ArtifactStatus.READY,
        sha256="b" * 64,
    )


def _capture_render(value: object, *, json_output: bool = False) -> str:
    console = _captured_console()
    with console.capture() as captured:
        PresentationAdapter(console).render(value, json_output=json_output)
    return captured.get()


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


@pytest.mark.parametrize(
    ("result", "expected"),
    (
        (SuccessResult("[bold]Saved.[/bold]"), "[bold]Saved.[/bold]\n"),
        (
            TableResult(columns=("name",), rows=(("[bold]Ada[/bold]",),)),
            '{"name": "[bold]Ada[/bold]"}\n',
        ),
        (MarkdownResult("# [bold]Report[/bold]\n"), "# [bold]Report[/bold]\n"),
        (
            WarningResult("RESULT_PARTIAL", "Some records were skipped."),
            "[RESULT_PARTIAL] Some records were skipped.\n",
        ),
        (
            ErrorResult(
                ErrorEnvelope(
                    code="RESULT_INVALID",
                    message="The [bold]result[/bold] is invalid.",
                    remediation="Retry with valid input.",
                    correlation_ref=None,
                )
            ),
            "[RESULT_INVALID] The [bold]result[/bold] is invalid.\n"
            "How to fix: Retry with valid input.\n",
        ),
        (
            StructuredResult({"label": "[bold]Ada[/bold]"}),
            '{\n  "label": "[bold]Ada[/bold]"\n}\n',
        ),
        (
            FileArtifactResult(_artifact()),
            json.dumps(_artifact().to_serializable(), indent=2, sort_keys=True) + "\n",
        ),
    ),
)
def test_adapter_renders_each_declared_result_through_injected_console(
    result: object,
    expected: str,
) -> None:
    assert _capture_render(result) == expected


def test_adapter_renders_json_through_injected_console_without_markup_interpretation() -> None:
    assert (
        _capture_render(
            StructuredResult({"label": "[bold]Ada[/bold]"}),
            json_output=True,
        )
        == '{\n  "label": "[bold]Ada[/bold]"\n}\n'
    )


def test_adapter_keeps_json_output_machine_readable_at_narrow_console_width() -> None:
    value = {"message": "x" * 100}
    console = Console(
        file=StringIO(),
        force_terminal=False,
        color_system=None,
        highlight=False,
        width=20,
    )

    with console.capture() as captured:
        PresentationAdapter(console).render(StructuredResult(value), json_output=True)

    assert json.loads(captured.get()) == value
