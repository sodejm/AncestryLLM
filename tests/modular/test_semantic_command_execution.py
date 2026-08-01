from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ancestryllm.application.executor import CommandInvocation
from ancestryllm.application.results import FileArtifactResult, TableResult
from ancestryllm.core.context import AppContext
from ancestryllm.core.ingress import FileIngressPolicy
from ancestryllm.execution.common import structured_result, table_result
from ancestryllm.execution.database import DatabaseExecutor
from ancestryllm.execution.gedcom import GedcomExecutor
from ancestryllm.execution.modules import ModulesExecutor
from ancestryllm.execution.rootsmagic import RootsMagicExecutor
from ancestryllm.terminal.parser import build_parser, invocation_from_namespace


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


def test_rootsmagic_list_returns_stable_consumable_tree_records(
    tmp_path: Path,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = tmp_path / "Fictional.rmtree"
    tree.write_bytes(b"fictional rootsmagic database")
    selected_refs: list[str] = []
    monkeypatch.setattr(
        "ancestryllm.rootsmagic.service.RootsMagicService.list_trees",
        lambda _self: [tree],
    )
    monkeypatch.setattr(
        "ancestryllm.rootsmagic.service.RootsMagicService.query_sql",
        lambda _self, tree_ref, _sql: selected_refs.append(tree_ref) or {"rows": []},
    )

    def export(_self: object, tree_ref: str, output: Path, **kwargs: object) -> object:
        selected_refs.append(tree_ref)
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

    assert isinstance(outcome.result, TableResult)
    assert isinstance(serialized, list)
    assert serialized == [
        {
            "tree_ref": "Fictional.rmtree",
            "label": "Fictional",
            "immutable": True,
        }
    ]
    assert str(tree) not in json.dumps(serialized)

    tree_ref = serialized[0]["tree_ref"]
    assert isinstance(tree_ref, str)
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
                str(tmp_path / "family.ged"),
                "--report",
                str(tmp_path / "family.export.md"),
            ]
        )
    )

    assert selected_refs == [tree_ref, tree_ref]


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
