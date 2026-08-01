from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ancestryllm.application.executor import CommandInvocation
from ancestryllm.application.results import FileArtifactResult, TableResult
from ancestryllm.core.context import AppContext
from ancestryllm.execution.database import DatabaseExecutor
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


def test_rootsmagic_list_returns_path_free_table_artifacts(
    tmp_path: Path,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = tmp_path / "Fictional.rmtree"
    tree.write_bytes(b"fictional rootsmagic database")
    monkeypatch.setattr(
        "ancestryllm.rootsmagic.service.RootsMagicService.list_trees",
        lambda _self: [tree],
    )

    outcome = RootsMagicExecutor(app_context)(_invocation(["rootsmagic", "list"]))
    serialized = outcome.result.to_serializable()

    assert isinstance(outcome.result, TableResult)
    assert isinstance(serialized, list)
    assert len(serialized) == 1
    assert isinstance(serialized[0], dict)
    assert "artifact_id" in serialized[0]
    assert str(tree) not in json.dumps(serialized)


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
