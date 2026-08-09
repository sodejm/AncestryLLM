"""Regression tests for the local-only wiki documentation preflight."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_wiki_docs.py"
sys.path.insert(0, str(_SCRIPT.parent))
_SPEC = importlib.util.spec_from_file_location("validate_wiki_docs", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
wiki_validation = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = wiki_validation
_SPEC.loader.exec_module(wiki_validation)


def _write_wiki(source: Path, *, sidebar: str = "[[Guide]]") -> None:
    (source / "Home.md").write_text("# Home\n", encoding="utf-8")
    (source / "Guide.md").write_text("# Guide\n", encoding="utf-8")
    (source / "_Sidebar.md").write_text(sidebar, encoding="utf-8")


def test_valid_source_requires_no_wiki_checkout(tmp_path: Path) -> None:
    _write_wiki(tmp_path)

    assert wiki_validation.main(["--source", str(tmp_path)]) == 0


def test_missing_home_is_rejected(tmp_path: Path, capsys) -> None:
    (tmp_path / "_Sidebar.md").write_text("", encoding="utf-8")

    assert wiki_validation.main(["--source", str(tmp_path)]) == 1
    assert "required page is missing: Home.md" in capsys.readouterr().err


def test_sidebar_targets_must_exist(tmp_path: Path, capsys) -> None:
    _write_wiki(tmp_path, sidebar="[[Guide]]\n[Missing](Missing.md)")

    assert wiki_validation.main(["--source", str(tmp_path)]) == 1
    assert "broken sidebar target: Missing.md" in capsys.readouterr().err


def test_duplicate_page_names_are_rejected(tmp_path: Path, capsys) -> None:
    _write_wiki(tmp_path)
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "Guide.md").write_text("# nested guide\n", encoding="utf-8")

    assert wiki_validation.main(["--source", str(tmp_path)]) == 1
    output = capsys.readouterr().err
    assert "duplicate wiki page name: Guide.md and nested/Guide.md" in output


def test_case_insensitive_filename_collisions_are_rejected(tmp_path: Path) -> None:
    # APFS is commonly case-insensitive, so construct the paths directly rather
    # than requiring this fixture to create two files that its filesystem cannot hold.
    errors = wiki_validation._source_errors(
        tmp_path,
        [tmp_path / "Home.md", tmp_path / "Guide.md", tmp_path / "guide.md"],
    )

    assert [error.message for error in errors] == [
        "case-insensitive filename collision: Guide.md and guide.md",
        "duplicate wiki page name: Guide.md and guide.md",
    ]


def test_unsafe_sidebar_paths_are_rejected(tmp_path: Path, capsys) -> None:
    _write_wiki(tmp_path, sidebar="[[../Secret]]\n[Etc](/etc/passwd)\n[Drive](C:/secret)")

    assert wiki_validation.main(["--source", str(tmp_path)]) == 1
    output = capsys.readouterr().err
    assert "unsafe sidebar target: ../Secret" in output
    assert "unsafe sidebar target: /etc/passwd" in output
    assert "unsafe sidebar target: C:/secret" in output


def test_wiki_style_sidebar_links_cannot_address_nested_source_paths(
    tmp_path: Path, capsys
) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "Home.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "nested" / "Child.md").write_text("# Child\n", encoding="utf-8")
    (tmp_path / "_Sidebar.md").write_text("[[nested/Child]]\n", encoding="utf-8")

    assert wiki_validation.main(["--source", str(tmp_path)]) == 1
    assert "unsafe sidebar target: nested/Child" in capsys.readouterr().err


def test_symlinked_sources_are_rejected(tmp_path: Path, capsys) -> None:
    _write_wiki(tmp_path)
    outside = tmp_path.parent / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    (tmp_path / "linked.md").symlink_to(outside)

    assert wiki_validation.main(["--source", str(tmp_path)]) == 1
    assert "symlinked source is not supported: linked.md" in capsys.readouterr().err


def test_all_local_links_and_metadata_are_validated(tmp_path: Path, capsys) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "_data").mkdir()
    (tmp_path / "Home.md").write_text("[Child](nested/Child.md)\n", encoding="utf-8")
    (tmp_path / "_Sidebar.md").write_text("[Child](nested/Child.md)\n", encoding="utf-8")
    (tmp_path / "nested" / "Child.md").write_text(
        "[Parent](../Home.md) ![Missing](../assets/missing.svg)\n",
        encoding="utf-8",
    )
    (tmp_path / "_data" / "page_metadata.json").write_text(
        '{"Home.md": {"title": "Home", "description": "Home description"}}',
        encoding="utf-8",
    )

    assert wiki_validation.main(["--source", str(tmp_path)]) == 1
    output = capsys.readouterr().err
    assert "broken local target: nested/Child.md -> ../assets/missing.svg" in output
    assert "missing page metadata: nested/Child.md" in output


def test_local_source_anchors_are_validated_after_percent_decoding(tmp_path: Path, capsys) -> None:
    (tmp_path / "Home.md").write_text(
        "[Valid](Guide.md#caf%C3%A9)\n[Missing](Guide.md#not-there)\n",
        encoding="utf-8",
    )
    (tmp_path / "Guide.md").write_text("# Café\n", encoding="utf-8")
    (tmp_path / "_Sidebar.md").write_text("[Guide](Guide.md)\n", encoding="utf-8")

    assert wiki_validation.main(["--source", str(tmp_path)]) == 1
    output = capsys.readouterr().err
    assert "missing source anchor: Home.md -> Guide.md#not-there" in output
    assert "Guide.md#caf%C3%A9" not in output


def test_query_only_same_page_anchor_is_validated(tmp_path: Path, capsys) -> None:
    (tmp_path / "Home.md").write_text(
        "# Home\n\n## Topic\n\n[Filtered view](?view=compact#topic)\n",
        encoding="utf-8",
    )
    (tmp_path / "_Sidebar.md").write_text("[Home](Home.md)\n", encoding="utf-8")

    assert wiki_validation.main(["--source", str(tmp_path)]) == 0
    assert capsys.readouterr().err == ""


def test_setext_heading_anchors_are_validated(tmp_path: Path, capsys) -> None:
    (tmp_path / "Home.md").write_text(
        "[Setext section](Guide.md#setext-section)\n", encoding="utf-8"
    )
    (tmp_path / "Guide.md").write_text("Setext Section\n==============\n", encoding="utf-8")
    (tmp_path / "_Sidebar.md").write_text("[Guide](Guide.md)\n", encoding="utf-8")

    assert wiki_validation.main(["--source", str(tmp_path)]) == 0
    assert capsys.readouterr().err == ""
