"""Tests for the deterministic all-tracked-Markdown validation gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.check_gfm_markdown import MarkdownValidationError, validate_markdown


def test_gfm_tables_strikethrough_tasks_and_fences_are_accepted() -> None:
    markdown = """\
# Example

| State | Value |
| --- | --- |
| done | ~~old~~ new |

- [x] checked

```python
print("safe")
```
"""

    validate_markdown(Path("example.md"), markdown)


def test_unclosed_fence_fails_with_stable_code() -> None:
    with pytest.raises(MarkdownValidationError) as raised:
        validate_markdown(Path("broken.md"), "# Broken\n\n```python\nprint('open')\n")

    assert raised.value.code == "GFM_UNCLOSED_FENCE"
    assert raised.value.path == Path("broken.md")


def test_disallowed_control_character_fails_with_stable_code() -> None:
    with pytest.raises(MarkdownValidationError) as raised:
        validate_markdown(Path("broken.md"), "# Broken\n\nvalue\x00\n")

    assert raised.value.code == "GFM_CONTROL_CHARACTER"


def test_repository_validation_covers_every_tracked_markdown_file() -> None:
    from scripts.check_gfm_markdown import tracked_markdown_paths

    root = Path(__file__).resolve().parents[1]
    paths = tracked_markdown_paths(root)

    assert paths
    assert root / "README.md" in paths
    assert root / "docs" / "THREAT_MODEL.md" in paths
    for path in paths:
        validate_markdown(path.relative_to(root), path.read_text(encoding="utf-8"))
