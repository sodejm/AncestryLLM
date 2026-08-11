"""Verify semantic command executors return stable path-free result DTOs."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ancestryllm.application.results import FileArtifactResult, TableResult
from ancestryllm.core.ingress import FileIngressPolicy
from ancestryllm.execution.common import structured_result, table_result
from ancestryllm.execution.database import DatabaseExecutor
from ancestryllm.execution.gedcom import GedcomExecutor
from ancestryllm.execution.modules import ModulesExecutor
from ancestryllm.execution.rootsmagic import RootsMagicExecutor
from ancestryllm.terminal.parser import build_parser, invocation_from_namespace

if TYPE_CHECKING:
    from ancestryllm.application.executor import CommandInvocation
    from ancestryllm.core.context import AppContext


def _invocation(tokens: list[str]) -> CommandInvocation:
    return invocation_from_namespace(build_parser().parse_args(tokens))


def test_tabular_handlers_return_table_results(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ancestryllm.storage.diagnostics.diagnose_storage",
        lambda *_args: [{"code": "STORAGE_OK", "status": "ok", "message": "Ready"}],
    )

    modules = ModulesExecutor(app_context)(_invocation(["modules", "list"]))
    diagnostics = DatabaseExecutor(app_context)(_invocation(["database", "diagnose"]))

    assert isinstance(modules.result, TableResult)
    assert isinstance(diagnostics.result, TableResult)


def test_generic_structured_and_table_results_reject_host_paths() -> None:
    with pytest.raises(TypeError, match="Path"):
        structured_result({"output": Path("private.ged")})
    with pytest.raises(TypeError, match="Path"):
        table_result(("output",), ({"output": Path("private.ged")},))


def test_database_backup_returns_a_path_free_file_artifact(
    tmp_path: Path,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "PRIVATE-HOST-PATH" / "encrypted-backup.db"
    destination.parent.mkdir()

    def backup(_self: object, selected_destination: Path) -> None:
        assert selected_destination == destination.resolve()
        selected_destination.write_bytes(b"fictional encrypted backup")

    monkeypatch.setattr(type(app_context.database), "backup", backup)

    outcome = DatabaseExecutor(app_context)(_invocation(["database", "backup", str(destination)]))
    serialized = outcome.result.to_serializable()

    assert isinstance(outcome.result, FileArtifactResult)
    assert isinstance(serialized, dict)
    assert serialized["artifact_type"] == "encrypted_database_backup"
    assert serialized["media_type"] == "application/octet-stream"
    assert serialized["status"] == "ready"
    assert str(destination) not in json.dumps(serialized)
    assert "PRIVATE-HOST-PATH" not in json.dumps(serialized)


def test_rootsmagic_list_returns_stable_consumable_tree_records(
    tmp_path: Path,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_tree = tmp_path / "PRIVATE-FIRST-ROOT" / "Fictional.rmtree"
    second_tree = tmp_path / "PRIVATE-SECOND-ROOT" / "Fictional.rmtree"
    first_tree.parent.mkdir()
    second_tree.parent.mkdir()
    first_tree.write_bytes(b"first fictional rootsmagic database")
    second_tree.write_bytes(b"second fictional rootsmagic database")
    trees = [first_tree, second_tree]
    selected_trees: list[Path] = []
    monkeypatch.setattr(
        "ancestryllm.rootsmagic.service.RootsMagicService.list_trees",
        lambda _self: trees,
    )

    def query_sql(_self: object, selected_tree: Path, _sql: str) -> dict[str, list[object]]:
        selected_trees.append(selected_tree)
        return {"rows": []}

    monkeypatch.setattr(
        "ancestryllm.rootsmagic.service.RootsMagicService.query_sql",
        query_sql,
    )

    def export(_self: object, selected_tree: Path, output: Path, **kwargs: object) -> object:
        selected_trees.append(selected_tree)
        report = kwargs["report_path"]
        assert isinstance(report, Path)
        output.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
        report.write_text("# Export report\n", encoding="utf-8")
        return SimpleNamespace(output_path=output, report_path=report)

    monkeypatch.setattr(
        "ancestryllm.rootsmagic.service.RootsMagicService.export",
        export,
    )

    outcome = RootsMagicExecutor(app_context)(_invocation(["rootsmagic", "list"]))
    serialized = outcome.result.to_serializable()
    repeated = RootsMagicExecutor(app_context)(_invocation(["rootsmagic", "list"])).result

    assert isinstance(outcome.result, TableResult)
    assert isinstance(serialized, list)
    assert len(serialized) == 2
    assert repeated.to_serializable() == serialized
    assert [record["label"] for record in serialized] == ["Fictional", "Fictional"]
    assert all(record["immutable"] is True for record in serialized)
    tree_refs = [record["tree_ref"] for record in serialized]
    assert all(isinstance(tree_ref, str) and tree_ref.startswith("tree_") for tree_ref in tree_refs)
    assert len(set(tree_refs)) == 2
    serialized_json = json.dumps(serialized)
    assert str(first_tree.parent) not in serialized_json
    assert str(second_tree.parent) not in serialized_json

    for index, (tree_ref, expected_tree) in enumerate(zip(tree_refs, trees, strict=True)):
        RootsMagicExecutor(app_context)(
            _invocation(["rootsmagic", "query", "--tree", tree_ref, "--sql", "SELECT 1"])
        )
        RootsMagicExecutor(app_context)(
            _invocation(
                [
                    "rootsmagic",
                    "export",
                    "--tree",
                    tree_ref,
                    "--output",
                    str(tmp_path / f"family-{index}.ged"),
                    "--report",
                    str(tmp_path / f"family-{index}.export.md"),
                ]
            )
        )
        assert selected_trees[-2:] == [expected_tree, expected_tree]

    assert selected_trees == [first_tree, first_tree, second_tree, second_tree]


@pytest.mark.parametrize("action", ("merge", "subtree", "quality"))
def test_gedcom_file_commands_return_path_free_file_artifacts(
    action: str,
    tmp_path: Path,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.ged"
    source.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    output = tmp_path / ("quality.md" if action == "quality" else "output.ged")
    report = tmp_path / "quality.md"

    def merge(_self: object, *_args: object, **_kwargs: object) -> object:
        output.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
        report.write_text("# Quality\n", encoding="utf-8")
        return SimpleNamespace(output_path=output, quality_path=report)

    def subtree(_self: object, *_args: object, **_kwargs: object) -> object:
        output.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
        return SimpleNamespace(output_path=output, quality_path=None)

    def quality(_self: object, *_args: object, **_kwargs: object) -> Path:
        output.write_text("# Quality\n", encoding="utf-8")
        return output

    monkeypatch.setattr(
        f"ancestryllm.gedcom.service.GedcomService.{action}",
        {"merge": merge, "subtree": subtree, "quality": quality}[action],
    )
    arguments = {
        "merge": [
            "gedcom",
            "merge",
            str(source),
            "--output",
            str(output),
            "--quality-report",
            str(report),
        ],
        "subtree": [
            "gedcom",
            "subtree",
            str(source),
            "--output",
            str(output),
            "--root-person",
            "Ada Example",
        ],
        "quality": [
            "gedcom",
            "quality",
            str(source),
            "--output",
            str(output),
            "--root-person",
            "Ada Example",
        ],
    }[action]

    outcome = GedcomExecutor(
        app_context,
        FileIngressPolicy(app_context.config.file_ingress),
    )(_invocation(arguments))
    serialized = outcome.result.to_serializable()

    assert isinstance(outcome.result, FileArtifactResult)
    assert str(output) not in json.dumps(serialized)
    assert str(report) not in json.dumps(serialized)


def test_rootsmagic_export_returns_path_free_file_artifacts(
    tmp_path: Path,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "family.ged"
    report = tmp_path / "family.export.md"

    def export(_self: object, _tree: str, selected_output: Path, **kwargs: object) -> object:
        selected_report = kwargs["report_path"]
        assert isinstance(selected_report, Path)
        selected_output.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
        selected_report.write_text("# Export report\n", encoding="utf-8")
        return SimpleNamespace(output_path=selected_output, report_path=selected_report)

    monkeypatch.setattr(
        "ancestryllm.rootsmagic.service.RootsMagicService.export",
        export,
    )

    outcome = RootsMagicExecutor(app_context)(
        _invocation(
            [
                "rootsmagic",
                "export",
                "--tree",
                "Fictional.rmtree",
                "--output",
                str(output),
                "--report",
                str(report),
            ]
        )
    )
    serialized = outcome.result.to_serializable()

    assert isinstance(outcome.result, FileArtifactResult)
    assert isinstance(serialized, dict)
    assert len(serialized["related_artifacts"]) == 1
    assert str(output) not in json.dumps(serialized)
    assert str(report) not in json.dumps(serialized)
