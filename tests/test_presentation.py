from __future__ import annotations

from io import StringIO

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


def test_to_plain_converts_paths_without_rendering_them() -> None:
    from pathlib import Path

    assert to_plain({"output": Path("report.json")}) == {"output": "report.json"}
