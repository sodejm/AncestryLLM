"""Verify CLI and console routing, serialization, and sanitized error behavior."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

from ancestryllm.application.deployment import DeploymentService
from ancestryllm.cli import _descriptor_payload, main
from ancestryllm.console.presentation import PresentationAdapter, to_plain
from ancestryllm.console.router import RouteKind, SessionRouter
from ancestryllm.core.commands import BUILTIN_MODULES, COMMAND_SPECIFICATIONS
from ancestryllm.core.deployment import DeploymentProfile
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.modules import ModuleRegistry
from ancestryllm.gedcom.service import GedcomSyncResult
from ancestryllm.storage.diagnostics import diagnose_storage

if TYPE_CHECKING:
    from collections.abc import Callable

    from ancestryllm.core.context import AppContext


def _expected_tree_ref(tree: Path) -> str:
    return f"tree_{hashlib.sha256(os.fsencode(str(tree.resolve(strict=True)))).hexdigest()}"


@dataclass(frozen=True, slots=True)
class CommandCase:
    module: str
    action: str
    arguments: tuple[str, ...]
    expected: Any | Callable[[AppContext], Any]

    @property
    def tokens(self) -> list[str]:
        return [self.module, self.action, *self.arguments]


@dataclass(frozen=True, slots=True)
class OpaqueArtifactExpectation:
    artifacts: tuple[tuple[str, str], ...]
    file_result: bool = False


@pytest.fixture
def fictional_files(tmp_path: Path) -> dict[str, Path]:
    gedcom = tmp_path / "fictional-tree.ged"
    gedcom.write_text(
        "0 HEAD\n1 GEDC\n2 VERS 5.5.5\n0 @I1@ INDI\n1 NAME Ada /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    ocr = tmp_path / "fictional-ocr.txt"
    ocr.write_text("Ada Example was born in Fiction County.", encoding="utf-8")
    schema = tmp_path / "fictional-schema.json"
    schema.write_text('{"type": "object"}', encoding="utf-8")
    rootsmagic = tmp_path / "fictional-tree.rmtree"
    rootsmagic.write_bytes(b"fictional RootsMagic database")
    return {
        "gedcom": gedcom,
        "ocr": ocr,
        "rootsmagic": rootsmagic,
        "schema": schema,
        "output": tmp_path / "fictional-output.ged",
        "report": tmp_path / "fictional-report.json",
        "backup": tmp_path / "fictional-backup.db",
    }


@pytest.fixture
def command_cases(
    fictional_files: dict[str, Path], app_context: AppContext
) -> tuple[CommandCase, ...]:
    gedcom = str(fictional_files["gedcom"])
    output = str(fictional_files["output"])
    report = str(fictional_files["report"])
    ocr = str(fictional_files["ocr"])
    schema = str(fictional_files["schema"])
    backup = str(fictional_files["backup"])
    deployment = DeploymentService(app_context.config)
    local = DeploymentProfile.local()
    deployment_preview = deployment.preview(
        local,
        schema_version=local.schema_version,
        expected_revision=app_context.config.revision,
    )
    target_arguments = (
        "--mode",
        local.mode.value,
        "--schema-version",
        str(local.schema_version),
        "--expected-revision",
        str(app_context.config.revision),
    )
    return (
        CommandCase("deployment", "modes", (), deployment.modes()),
        CommandCase("deployment", "status", (), deployment.snapshot()),
        CommandCase(
            "deployment",
            "preview",
            target_arguments,
            deployment_preview,
        ),
        CommandCase(
            "deployment",
            "switch",
            (
                *target_arguments,
                "--confirm",
                deployment_preview.confirmation,
                "--unattended",
            ),
            deployment.snapshot(),
        ),
        CommandCase("deployment", "diagnose", (), deployment.diagnose()),
        CommandCase(
            "deployment",
            "metadata",
            ("--purpose", "support"),
            deployment.metadata("support"),
        ),
        CommandCase(
            "rootsmagic",
            "list",
            (),
            [
                {
                    "tree_ref": _expected_tree_ref(fictional_files["rootsmagic"]),
                    "label": fictional_files["rootsmagic"].stem,
                    "immutable": True,
                }
            ],
        ),
        CommandCase(
            "rootsmagic",
            "query",
            ("--tree", "Fictional.rmtree", "--sql", "SELECT 1"),
            {"module": "rootsmagic", "action": "query"},
        ),
        CommandCase(
            "rootsmagic",
            "export",
            (
                "--tree",
                "Fictional.rmtree",
                "--output",
                output,
                "--root-person-id",
                "I1",
                "--living",
                "redact",
            ),
            OpaqueArtifactExpectation(
                (
                    ("gedcom_export", "text/vnd.familysearch.gedcom"),
                    ("export_report", "text/markdown"),
                ),
                file_result=True,
            ),
        ),
        CommandCase(
            "gedcom",
            "merge",
            (gedcom, "--output", output, "--quality-report", report),
            OpaqueArtifactExpectation(
                (
                    ("gedcom_merge", "text/vnd.familysearch.gedcom"),
                    ("quality_report", "text/markdown"),
                ),
                file_result=True,
            ),
        ),
        CommandCase(
            "gedcom",
            "subtree",
            (gedcom, "--output", output, "--root-person", "Ada Example"),
            OpaqueArtifactExpectation(
                (("gedcom_subtree", "text/vnd.familysearch.gedcom"),),
                file_result=True,
            ),
        ),
        CommandCase(
            "gedcom",
            "quality",
            (gedcom, "--output", report, "--root-person", "Ada Example"),
            OpaqueArtifactExpectation(
                (("quality_report", "text/markdown"),),
                file_result=True,
            ),
        ),
        CommandCase(
            "gedcom",
            "sync",
            ("update", "--manifest", "fictional-private-manifest.json", "--dry-run"),
            GedcomSyncResult(exit_code=0, output=""),
        ),
        CommandCase(
            "ocr",
            "extract",
            ("--input", ocr, "--provider", "none", "--model", "offline"),
            {"module": "ocr", "action": "extract"},
        ),
        CommandCase("prompts", "list", (), {"module": "prompts", "action": "list"}),
        CommandCase(
            "prompts",
            "save",
            (
                "family-summary",
                "--purpose",
                "Fictional research",
                "--body",
                "Hello ${person}",
                "--variable",
                "person",
                "--schema-file",
                schema,
                "--tag",
                "fictional",
                "--tag",
                "local",
            ),
            {"module": "prompts", "action": "save"},
        ),
        CommandCase(
            "prompts",
            "show",
            ("family-summary", "--version", "1"),
            {"module": "prompts", "action": "show"},
        ),
        CommandCase(
            "prompts",
            "render",
            ("family-summary", "--value", "person=Ada Example"),
            {"module": "prompts", "action": "render"},
        ),
        CommandCase(
            "people",
            "list",
            ("--workspace", "fictional"),
            {"module": "people", "action": "list"},
        ),
        CommandCase(
            "people",
            "add",
            (
                "Zoë 示例",
                "--living-status",
                "deceased",
                "--notes",
                "",
                "--workspace",
                "fictional",
            ),
            {"module": "people", "action": "add"},
        ),
        CommandCase("providers", "list", (), {"profiles": ["fictional"], "consents": ["local"]}),
        CommandCase(
            "providers",
            "create",
            ("fictional", "--provider", "ollama", "--model", "local-model"),
            {"module": "providers", "action": "create"},
        ),
        CommandCase(
            "providers",
            "consent",
            (
                "local",
                "--profile",
                "fictional",
                "--module",
                "gedcom",
                "--module",
                "ocr",
                "--purpose",
                "merge",
                "--purpose",
                "extract",
                "--data-class",
                "public_genealogy",
                "--model",
                "local-model",
            ),
            {"module": "providers", "action": "consent"},
        ),
        CommandCase(
            "providers",
            "revoke",
            ("local",),
            "Revoked consent: local",
        ),
        CommandCase(
            "secrets", "set", ("openai.api_key",), "Stored secret reference: openai.api_key"
        ),
        CommandCase(
            "secrets",
            "delete",
            ("openai.api_key",),
            "Deleted secret reference: openai.api_key",
        ),
        CommandCase("secrets", "status", ("openai.api_key",), {"openai.api_key": False}),
        CommandCase(
            "modules",
            "list",
            (),
            lambda context: [
                _descriptor_payload(item) for item in ModuleRegistry(context).descriptors()
            ],
        ),
        CommandCase("modules", "disable", ("ocr",), "Disabled module: ocr"),
        CommandCase("modules", "enable", ("ocr",), "Enabled module: ocr"),
        CommandCase(
            "database",
            "backup",
            (backup,),
            OpaqueArtifactExpectation(
                (("encrypted_database_backup", "application/octet-stream"),),
                file_result=True,
            ),
        ),
        CommandCase(
            "database",
            "diagnose",
            (),
            lambda context: diagnose_storage(context.database.path, context.secrets),
        ),
    )


@pytest.fixture
def mocked_action_services(
    app_context: AppContext,
    fictional_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ancestryllm.gedcom.service import GedcomService
    from ancestryllm.ocr.service import OcrService
    from ancestryllm.rootsmagic.service import RootsMagicService

    monkeypatch.setattr(
        RootsMagicService, "list_trees", lambda _self: [fictional_files["rootsmagic"]]
    )
    monkeypatch.setattr(
        RootsMagicService,
        "query_sql",
        lambda _self, *_args, **_kwargs: {"module": "rootsmagic", "action": "query"},
    )

    def export_rootsmagic(
        _self: RootsMagicService,
        _tree: str,
        output: Path,
        **kwargs: Any,
    ) -> SimpleNamespace:
        report = kwargs["report_path"] or output.resolve().with_suffix(".export.md")
        output.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
        report.write_text("# Fictional export report\n", encoding="utf-8")
        return SimpleNamespace(output_path=output, report_path=report)

    monkeypatch.setattr(RootsMagicService, "export", export_rootsmagic)

    def merge_gedcom(
        _self: GedcomService,
        _inputs: list[Path],
        output: Path,
        **kwargs: Any,
    ) -> SimpleNamespace:
        quality_report = kwargs["quality_path"]
        output.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
        if quality_report is not None:
            quality_report.write_text("# Fictional quality report\n", encoding="utf-8")
        return SimpleNamespace(output_path=output, quality_path=quality_report)

    def subtree_gedcom(
        _self: GedcomService,
        _source: Path,
        output: Path,
        **_kwargs: Any,
    ) -> SimpleNamespace:
        output.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
        return SimpleNamespace(output_path=output)

    def quality_gedcom(
        _self: GedcomService,
        _source: Path,
        output: Path,
        **_kwargs: Any,
    ) -> Path:
        output.write_text("# Fictional quality report\n", encoding="utf-8")
        return output

    monkeypatch.setattr(GedcomService, "merge", merge_gedcom)
    monkeypatch.setattr(GedcomService, "subtree", subtree_gedcom)
    monkeypatch.setattr(GedcomService, "quality", quality_gedcom)
    monkeypatch.setattr(
        GedcomService, "sync", lambda _self, _args: GedcomSyncResult(exit_code=0, output="")
    )
    monkeypatch.setattr(
        OcrService,
        "extract",
        lambda _self, *_args, **_kwargs: {"module": "ocr", "action": "extract"},
    )

    app_context.prompts = SimpleNamespace(
        list=lambda: {"module": "prompts", "action": "list"},
        save=lambda *_args: {"module": "prompts", "action": "save"},
        get=lambda *_args: {"module": "prompts", "action": "show"},
        render=lambda *_args: {"module": "prompts", "action": "render"},
    )
    app_context.research = SimpleNamespace(
        list_people=lambda *_args: {"module": "people", "action": "list"},
        add_person=lambda *_args: {"module": "people", "action": "add"},
    )
    app_context.provider_profiles = SimpleNamespace(
        list_profiles=lambda: ["fictional"],
        list_consents=lambda: ["local"],
        create_profile=lambda *_args: {"module": "providers", "action": "create"},
        create_consent=lambda *_args, **_kwargs: {"module": "providers", "action": "consent"},
        revoke_consent=lambda *_args: None,
        consent_grant=lambda *_args: None,
    )


def _record_rendered_values(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    rendered: list[Any] = []

    def record(_self: PresentationAdapter, value: Any, *, json_output: bool = False) -> None:
        del json_output
        rendered.append(to_plain(value))

    monkeypatch.setattr(PresentationAdapter, "render", record)
    return rendered


def _expected_value(case: CommandCase, context: AppContext) -> Any:
    return case.expected(context) if callable(case.expected) else case.expected


def _assert_artifact_ref(
    value: Any,
    *,
    artifact_type: str,
    media_type: str,
) -> None:
    assert isinstance(value, dict)
    assert set(value) == {
        "artifact_id",
        "media_type",
        "artifact_type",
        "size_bytes",
        "status",
        "sha256",
    }
    assert value["artifact_id"].startswith("art_")
    assert len(value["artifact_id"]) == 68
    assert value["artifact_type"] == artifact_type
    assert value["media_type"] == media_type
    assert isinstance(value["size_bytes"], int)
    assert value["size_bytes"] > 0
    assert value["status"] == "ready"
    assert isinstance(value["sha256"], str)
    assert len(value["sha256"]) == 64
    assert all(character in "0123456789abcdef" for character in value["sha256"])


def _assert_expected(actual: Any, expected: Any) -> None:
    if not isinstance(expected, OpaqueArtifactExpectation):
        assert actual == to_plain(expected)
        return
    if expected.file_result:
        assert isinstance(actual, dict)
        if set(actual) == {"artifact", "related_artifacts"}:
            artifacts = [actual["artifact"], *actual["related_artifacts"]]
        else:
            artifacts = [actual]
    else:
        assert isinstance(actual, list)
        artifacts = actual
    assert len(artifacts) == len(expected.artifacts)
    for artifact, (artifact_type, media_type) in zip(artifacts, expected.artifacts, strict=True):
        _assert_artifact_ref(
            artifact,
            artifact_type=artifact_type,
            media_type=media_type,
        )


def test_action_matrix_covers_every_shipped_module_action(
    command_cases: tuple[CommandCase, ...],
) -> None:
    covered = {(case.module, case.action) for case in command_cases}
    shipped = {
        (module_id, action.name)
        for module_id, specification in COMMAND_SPECIFICATIONS.items()
        for action in specification.actions
    }
    assert covered == shipped


@pytest.mark.parametrize(
    "case_index",
    range(sum(len(specification.actions) for specification in COMMAND_SPECIFICATIONS.values())),
)
def test_one_shot_returns_expected_dtos_for_every_action(
    case_index: int,
    command_cases: tuple[CommandCase, ...],
    app_context: AppContext,
    mocked_action_services: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del mocked_action_services
    case = command_cases[case_index]
    rendered = _record_rendered_values(monkeypatch)
    secret_value = "fictional-secret-value"
    monkeypatch.setattr(
        "ancestryllm.cli.getpass.getpass",
        Mock(side_effect=[secret_value, secret_value, secret_value, secret_value]),
    )

    assert main(["--json", *case.tokens], app_context) == 0

    assert len(rendered) == 1
    _assert_expected(rendered[0], _expected_value(case, app_context))
    assert secret_value not in json.dumps(rendered, ensure_ascii=False)


def test_every_action_serializes_json_and_repl_routes_direct_and_module_context(
    command_cases: tuple[CommandCase, ...],
    app_context: AppContext,
    mocked_action_services: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Action parity stops at routing; prompt-loop responsiveness is issue #59."""
    del mocked_action_services
    secret_value = "fictional-secret-value"
    monkeypatch.setattr("ancestryllm.cli.getpass.getpass", Mock(side_effect=[secret_value] * 16))
    for case in command_cases:
        assert main(["--json", *case.tokens], app_context) == 0
        stdout, stderr = capsys.readouterr()
        _assert_expected(json.loads(stdout), _expected_value(case, app_context))
        assert stderr == ""
        router = SessionRouter(app_context)
        direct = router.route_tokens(tuple(case.tokens))
        assert direct.kind is RouteKind.EXECUTE
        assert direct.invocation is not None
        if case.module in BUILTIN_MODULES:
            assert router.route_tokens(("use", case.module)).kind is RouteKind.OUTPUT
            contextual = router.route_tokens(("run", case.action, *case.arguments))
            assert contextual.kind is RouteKind.EXECUTE
            assert contextual.invocation is not None
            assert contextual.invocation.namespace == direct.invocation.namespace


def test_one_shot_lists_enabled_modules(app_context: AppContext, capsys) -> None:
    assert main(["--json", "modules", "list"], app_context) == 0
    output = capsys.readouterr().out
    assert '"module_id": "gedcom"' in output
    assert '"module_id": "rootsmagic"' in output


def test_stable_service_error_code_and_exit_are_preserved(app_context: AppContext, capsys) -> None:
    def fail() -> None:
        raise AncestryError("PROMPT_STABLE_FAILURE", "Safe fictional failure.", exit_code=7)

    app_context.prompts = SimpleNamespace(list=fail)
    assert main(["prompts", "list"], app_context) == 7
    assert "[PROMPT_STABLE_FAILURE] Safe fictional failure." in capsys.readouterr().err


def test_nonlocal_profile_blocks_ordinary_commands_but_keeps_recovery_available(
    app_context: AppContext, capsys
) -> None:
    app_context.config.default_provider = "openai"
    app_context.config.deployment = DeploymentProfile.connect_remote(
        endpoint_origin="https://remote.example",
        endpoint_identity_sha256="a" * 64,
    )

    assert main(["modules", "list"], app_context) == 2
    error = capsys.readouterr().err
    assert "[DEPLOYMENT_RUNTIME_MISMATCH]" in error
    assert "remote.example" not in error

    assert main(["--json", "deployment", "status"], app_context) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["profile"]["mode"] == "connect-remote"
    assert main(["--json", "deployment", "diagnose"], app_context) == 0
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["status"] == "failed"


@pytest.mark.parametrize(
    ("arguments", "error_text"),
    (
        (["unknown"], "invalid choice"),
        (["people", "add"], "the following arguments are required"),
        (["rootsmagic", "query", "--tree", "fictional"], "one of the arguments"),
        (["providers", "create", "p", "--provider", "none", "--model", "m"], "invalid choice"),
        (
            [
                "deployment",
                "switch",
                "--mode",
                "local-desktop",
                "--schema-version",
                "1",
                "--expected-revision",
                "0",
                "--confirm",
                "confirm-deployment-fictional",
            ],
            "the following arguments are required",
        ),
    ),
)
def test_parser_failures_have_documented_exit_two(
    arguments: list[str], error_text: str, app_context: AppContext, capsys
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(arguments, app_context)
    assert raised.value.code == 2
    assert error_text in capsys.readouterr().err


def test_invalid_values_missing_files_and_json_parser_failures_are_sanitized(
    app_context: AppContext, tmp_path: Path, capsys
) -> None:
    assert (
        main(["prompts", "render", "fictional", "--value", "not-an-assignment"], app_context) == 1
    )
    assert "[ARGUMENT_INVALID]" in capsys.readouterr().err

    missing = tmp_path / "missing.txt"
    assert (
        main(
            [
                "ocr",
                "extract",
                "--input",
                str(missing),
                "--provider",
                "none",
                "--model",
                "offline",
            ],
            app_context,
        )
        == 2
    )
    missing_error = capsys.readouterr().err
    assert "[FILE_INPUT_UNREADABLE]" in missing_error
    assert "Traceback" not in missing_error

    invalid_schema = tmp_path / "invalid-schema.json"
    invalid_schema.write_text("{not json", encoding="utf-8")
    assert (
        main(
            [
                "prompts",
                "save",
                "fictional",
                "--purpose",
                "local",
                "--body",
                "text",
                "--schema-file",
                str(invalid_schema),
            ],
            app_context,
        )
        == 2
    )
    assert "[FILE_JSON_INVALID]" in capsys.readouterr().err


def test_explicit_config_launches_repl_and_json_alone_is_rejected(
    tmp_path: Path,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    import ancestryllm.cli as cli
    import ancestryllm.console.shell as shell

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[storage]\ndata_dir = "{tmp_path / "data"}"\n',
        encoding="utf-8",
    )
    loaded = []
    closed: list[bool] = []

    class FakeContext:
        def close(self) -> None:
            closed.append(True)

    fake_context = FakeContext()

    def build_context(config):
        loaded.append(config)
        return fake_context

    monkeypatch.setattr(cli.AppContext, "build", build_context)
    monkeypatch.setattr(shell, "run_repl", lambda context: 17 if context is fake_context else 99)

    assert main(["--config", str(config_path)]) == 17
    assert main([f"--config={config_path}"]) == 17
    assert [item.config_path for item in loaded] == [config_path, config_path]
    assert closed == [True, True]

    assert main(["--json"], app_context) == 2
    assert "[ARGUMENT_INVALID]" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_exit"),
    (
        (
            AncestryError("REPL_START_FAILED", "Fictional startup failure.", exit_code=7),
            "REPL_START_FAILED",
            7,
        ),
        (OSError("PRIVATE raw startup failure"), "INPUT_ERROR", 2),
    ),
)
def test_bare_repl_startup_failures_use_the_cli_error_boundary(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    failure: Exception,
    expected_code: str,
    expected_exit: int,
) -> None:
    import ancestryllm.console.shell as shell

    def fail_repl(_context: object) -> int:
        raise failure

    monkeypatch.setattr(shell, "run_repl", fail_repl)

    assert main([], app_context) == expected_exit
    rendered = capsys.readouterr().err
    assert f"[{expected_code}]" in rendered
    assert "PRIVATE" not in rendered


def test_owned_context_cleanup_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    import ancestryllm.cli as cli
    import ancestryllm.console.shell as shell

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[storage]\ndata_dir = "{tmp_path / "data"}"\n',
        encoding="utf-8",
    )

    class FakeContext:
        def close(self) -> None:
            raise OSError("PRIVATE cleanup failure")

    monkeypatch.setattr(cli.AppContext, "build", lambda _config: FakeContext())
    monkeypatch.setattr(shell, "run_repl", lambda _context: 17)

    assert main(["--config", str(config_path)]) == 2
    rendered = capsys.readouterr().err
    assert "[INPUT_ERROR]" in rendered
    assert "PRIVATE" not in rendered


def test_missing_explicit_config_never_starts_repl_or_creates_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    import ancestryllm.console.shell as shell

    missing = tmp_path / "private-missing-repl-config.toml"
    data_dir = tmp_path / "must-not-exist"
    repl_contexts: list[object] = []
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        shell,
        "run_repl",
        lambda context: repl_contexts.append(context) or 0,
    )

    assert main(["--config", str(missing)]) == 2

    rendered = capsys.readouterr().err
    assert "[FILE_INPUT_UNREADABLE]" in rendered
    assert str(missing) not in rendered
    assert repl_contexts == []
    assert not data_dir.exists()


@pytest.mark.parametrize("failure", ("nul-data-dir", "family-tree-resolution"))
def test_config_only_repl_normalizes_invalid_configured_paths_before_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    failure: str,
) -> None:
    import ancestryllm.console.shell as shell

    configured = tmp_path / "private-unresolvable"
    config_path = tmp_path / "config.toml"
    if failure == "nul-data-dir":
        payload = '[storage]\ndata_dir = "\\u0000private-data"\n'
    else:
        payload = f'[storage]\nfamily_tree_dirs = ["{configured}"]\n'
        original_resolve = Path.resolve

        def reject_configured_path(
            path: Path,
            *args: object,
            **kwargs: object,
        ) -> Path:
            if path == configured:
                raise OSError("private path resolution failure")
            return original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", reject_configured_path)
    config_path.write_text(payload, encoding="utf-8")
    data_dir = tmp_path / "must-not-exist"
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(data_dir))
    run_repl = Mock(return_value=0)
    monkeypatch.setattr(shell, "run_repl", run_repl)

    assert main(["--config", str(config_path)]) == 2

    rendered = capsys.readouterr().err
    expected_code = "FILE_NUL_BYTE_UNSUPPORTED" if failure == "nul-data-dir" else "CONFIG_INVALID"
    assert f"[{expected_code}]" in rendered
    assert "private-data" not in rendered
    assert str(configured) not in rendered
    assert "private path resolution failure" not in rendered
    assert not data_dir.exists()
    run_repl.assert_not_called()


def test_explicit_missing_config_and_malformed_gedcom_are_sanitized(
    tmp_path: Path,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    missing = tmp_path / "private-missing-config.toml"
    data_dir = tmp_path / "must-not-exist"
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(data_dir))

    assert main(["--config", str(missing), "modules", "list"]) == 2
    config_error = capsys.readouterr().err
    assert "[FILE_INPUT_UNREADABLE]" in config_error
    assert str(missing) not in config_error
    assert not data_dir.exists()

    malformed = tmp_path / "private-malformed.ged"
    private_line = "PRIVATE-PAYLOAD malformed genealogy"
    malformed.write_text(f"0 HEAD\n{private_line}\n0 TRLR\n", encoding="utf-8")
    valid = tmp_path / "valid.ged"
    valid.write_text(
        "0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME Ada /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    output = tmp_path / "output.ged"
    output.write_bytes(b"sentinel\n")

    assert (
        main(
            [
                "gedcom",
                "merge",
                str(malformed),
                str(valid),
                "--output",
                str(output),
            ],
            app_context,
        )
        == 2
    )
    gedcom_error = capsys.readouterr().err
    assert "[GEDCOM_PARSE_INVALID]" in gedcom_error
    assert private_line not in gedcom_error
    assert str(malformed) not in gedcom_error
    assert output.read_bytes() == b"sentinel\n"


def test_one_shot_unresolved_gedcom_root_preserves_coded_sanitized_error(
    tmp_path: Path,
    app_context: AppContext,
    capsys,
) -> None:
    source = tmp_path / "fictional-tree.ged"
    source.write_text(
        "0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME Ada /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    output = tmp_path / "subtree.ged"
    output.write_bytes(b"sentinel\n")
    requested = "PRIVATE-PAYLOAD fictional root person"

    assert (
        main(
            [
                "gedcom",
                "subtree",
                str(source),
                "--output",
                str(output),
                "--root-person",
                requested,
            ],
            app_context,
        )
        == 2
    )

    rendered = capsys.readouterr().err
    assert "[GEDCOM_ROOT_PERSON_UNRESOLVED]" in rendered
    assert requested not in rendered
    assert "ValueError" not in rendered
    assert output.read_bytes() == b"sentinel\n"


def test_disabled_modules_are_not_imported_or_dispatched(
    app_context: AppContext, capsys: pytest.CaptureFixture[str]
) -> None:
    app_context.config.enabled_modules = {"gedcom"}
    assert [item.module_id for item in ModuleRegistry(app_context).descriptors()] == ["gedcom"]
    assert (
        main(
            [
                "ocr",
                "extract",
                "--input",
                "missing-fictional-input.txt",
                "--provider",
                "none",
                "--model",
                "offline",
            ],
            app_context,
        )
        == 2
    )
    assert "[MODULE_DISABLED] Module is not enabled: ocr." in capsys.readouterr().err


def test_disabled_module_has_matching_repl_code_exit_and_message(
    app_context: AppContext, capsys: pytest.CaptureFixture[str]
) -> None:
    app_context.config.enabled_modules = {"gedcom"}
    assert (
        main(
            ["ocr", "extract", "--input", "fictional.txt", "--provider", "none", "--model", "x"],
            app_context,
        )
        == 2
    )
    one_shot = capsys.readouterr().err
    with pytest.raises(AncestryError) as raised:
        SessionRouter(app_context).route(
            "ocr extract --input fictional.txt --provider none --model x"
        )
    assert raised.value.code == "MODULE_DISABLED"
    assert raised.value.exit_code == 2
    assert raised.value.message in one_shot


def test_secret_values_never_reach_one_shot_status_output(
    app_context: AppContext, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    secret_value = "fictional-SUPER-SECRET-value"
    monkeypatch.setattr(
        "ancestryllm.cli.getpass.getpass", Mock(side_effect=[secret_value, secret_value])
    )
    assert main(["secrets", "set", "openai.api_key"], app_context) == 0
    assert main(["--json", "secrets", "status", "openai.api_key"], app_context) == 0
    json_output = capsys.readouterr().out
    assert json.loads(json_output[json_output.rfind("{") :]) == {"openai.api_key": True}
    assert secret_value not in json_output


def test_database_diagnostics_are_available_as_json(app_context: AppContext, capsys) -> None:
    assert main(["--json", "database", "diagnose"], app_context) == 0
    assert '"code": "SQLCIPHER_READY"' in capsys.readouterr().out


def test_database_json_results_hide_host_paths(
    app_context: AppContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_parent = tmp_path / "PRIVATE-HOST-DATABASE-PATH"
    private_parent.mkdir()
    backup_path = private_parent / "encrypted-backup.db"

    def backup(_self: object, destination: Path) -> None:
        destination.write_bytes(b"fictional encrypted backup")

    monkeypatch.setattr(type(app_context.database), "backup", backup)

    assert main(["--json", "database", "backup", str(backup_path)], app_context) == 0
    backup_output = capsys.readouterr().out
    backup_result = json.loads(backup_output)
    _assert_artifact_ref(
        backup_result,
        artifact_type="encrypted_database_backup",
        media_type="application/octet-stream",
    )
    assert str(private_parent) not in backup_output
    assert "PRIVATE-HOST-DATABASE-PATH" not in backup_output

    app_context.database.path = private_parent / "missing" / "workspace.db"
    assert main(["--json", "database", "diagnose"], app_context) == 0
    diagnostic_output = capsys.readouterr().out
    diagnostic_result = json.loads(diagnostic_output)
    assert "DATABASE_DIRECTORY_MISSING" in {item["code"] for item in diagnostic_result}
    assert str(private_parent) not in diagnostic_output
    assert "PRIVATE-HOST-DATABASE-PATH" not in diagnostic_output


def test_database_backup_collision_cli_error_hides_host_paths(
    app_context: AppContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_parent = tmp_path / "PRIVATE-USERNAME-CANARY" / "PRIVATE-HOME-CANARY"
    private_parent.mkdir(parents=True)
    destination = private_parent / "PRIVATE-FICTIONAL-FAMILY-BACKUP.db"
    destination.write_bytes(b"existing encrypted backup sentinel")

    assert main(["database", "backup", str(destination)], app_context) == 1

    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    normalized_rendered = " ".join(rendered.split())
    assert "[BACKUP_EXISTS] The backup destination already exists." in normalized_rendered
    assert (
        "How to fix: Choose a different destination or remove the existing item before retrying."
        in normalized_rendered
    )
    for private_value in (
        str(destination),
        destination.name,
        "PRIVATE-USERNAME-CANARY",
        "PRIVATE-HOME-CANARY",
        str(app_context.database.path),
    ):
        assert private_value not in rendered
    assert destination.read_bytes() == b"existing encrypted backup sentinel"


def test_clean_install_entry_points_and_json_smoke(tmp_path: Path) -> None:
    assert (3, 12) <= sys.version_info[:2] < (3, 15)
    repository = Path(__file__).resolve().parents[2]
    wheelhouse = tmp_path / "wheelhouse"
    uv_name = "uv.exe" if os.name == "nt" else "uv"
    uv = repository / ".tools" / "uv" / uv_name
    assert uv.is_file(), "make setup must install the verified repository-local uv"
    uv_environment = {
        **os.environ,
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_PYTHON": sys.executable,
    }
    build = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(wheelhouse)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=repository,
        env=uv_environment,
    )
    assert build.returncode == 0, build.stderr
    wheel = next(wheelhouse.glob("ancestryllm-*.whl"))

    environment = tmp_path / "clean-install"
    venv.EnvBuilder(with_pip=False).create(environment)
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    requirements = tmp_path / "requirements.txt"
    export = subprocess.run(
        [
            uv,
            "export",
            "--locked",
            "--no-emit-project",
            "--no-hashes",
            "--output-file",
            str(requirements),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=repository,
        env=uv_environment,
    )
    assert export.returncode == 0, export.stderr
    install = subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--requirement",
            str(requirements),
            "--no-deps",
            str(wheel),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=uv_environment,
    )
    assert install.returncode == 0, install.stderr

    isolated_home = tmp_path / "fictional-home"
    isolated_home.mkdir()
    child_environment = {
        **os.environ,
        "HOME": str(isolated_home),
        "XDG_CONFIG_HOME": str(isolated_home / "config"),
        "XDG_DATA_HOME": str(isolated_home / "data"),
    }
    child_environment.pop("PYTHONPATH", None)

    location = subprocess.run(
        [str(python), "-I", "-c", "import ancestryllm; print(ancestryllm.__file__)"],
        check=False,
        capture_output=True,
        text=True,
        env=child_environment,
        cwd=isolated_home,
        timeout=30,
    )
    assert location.returncode == 0, location.stderr
    assert str(environment) in location.stdout
    assert str(repository) not in location.stdout

    for command in (
        [str(scripts / "ancestry"), "--json", "modules", "list"],
        [str(python), "-I", "-m", "ancestryllm", "--json", "modules", "list"],
    ):
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=child_environment,
            cwd=isolated_home,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)
        assert {item["module_id"] for item in payload} == set(BUILTIN_MODULES)
        assert completed.stderr == ""
