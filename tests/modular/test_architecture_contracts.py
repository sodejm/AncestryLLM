"""Executable dependency and public-façade contract tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from scripts.check_architecture_contracts import (
    CHARACTERIZATION_IMPORT_EXCEPTIONS,
    PUBLIC_FACADE_MODULES,
    TEMPORARY_EXCEPTIONS,
    DependencyException,
    check_repository_consumers,
    check_tree,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ancestryllm"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _write_module(root: Path, module: str, source: str) -> None:
    relative = module.removeprefix("ancestryllm").lstrip(".").replace(".", "/")
    path = root / (relative + ".py" if relative else "__init__.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _codes(root: Path, **kwargs: object) -> set[str]:
    report = check_tree(root, **kwargs)  # type: ignore[arg-type]
    return {violation.code for violation in report.violations}


def test_repository_dependency_contract_passes_with_only_live_exceptions() -> None:
    report = check_tree(PACKAGE_ROOT)

    assert report.passed
    assert report.used_exceptions == frozenset(TEMPORARY_EXCEPTIONS)


def test_repository_consumers_use_supported_facades() -> None:
    report = check_repository_consumers(REPOSITORY_ROOT)

    assert report.passed
    assert report.used_exceptions == frozenset(CHARACTERIZATION_IMPORT_EXCEPTIONS)


def test_repository_consumer_gate_rejects_private_gedcom_import(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    consumer = repository_root / "tests" / "test_feature.py"
    consumer.parent.mkdir(parents=True)
    consumer.write_text(
        "from ancestryllm.gedcom.engine import GedcomRecord\n",
        encoding="utf-8",
    )

    report = check_repository_consumers(
        repository_root,
        exceptions=(),
        require_all_exceptions=False,
    )

    assert {violation.code for violation in report.violations} == {"ARCH501"}


@pytest.mark.parametrize(
    "source",
    (
        "from ancestryllm.gedcom.identity import _country_from_place\n",
        "from ancestryllm.gedcom import identity as gm\ngm._country_from_place('Boston')\n",
        "import ancestryllm.gedcom.quality as quality\nquality._duplicate_pairs([])\n",
    ),
)
def test_repository_consumer_gate_rejects_private_facade_symbol(
    tmp_path: Path,
    source: str,
) -> None:
    repository_root = tmp_path / "repository"
    consumer = repository_root / "tests" / "test_feature.py"
    consumer.parent.mkdir(parents=True)
    consumer.write_text(source, encoding="utf-8")

    report = check_repository_consumers(
        repository_root,
        exceptions=(),
        require_all_exceptions=False,
    )

    assert {violation.code for violation in report.violations} == {"ARCH503"}


@pytest.mark.parametrize(
    "relative_path",
    (
        "gedcom/engine.py",
        "gedcom/incremental.py",
        "gedcom/service.py",
        "gedcom/sync_kernel.py",
    ),
)
def test_gedcom_kernel_and_service_paths_do_not_perform_terminal_io(
    relative_path: str,
) -> None:
    source = (PACKAGE_ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source)

    terminal_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"input", "print"}
    }
    captured_stream_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in {"contextlib", "io"}
        for alias in node.names
        if alias.name in {"StringIO", "redirect_stderr", "redirect_stdout"}
    }

    assert terminal_calls == set()
    assert captured_stream_imports == set()


def test_every_declared_public_facade_has_a_literal_bound_allowlist() -> None:
    report = check_tree(
        PACKAGE_ROOT,
        exceptions=TEMPORARY_EXCEPTIONS,
        require_all_exceptions=False,
    )

    facade_errors = {
        violation.code
        for violation in report.violations
        if violation.code in {"ARCH001", "ARCH002", "ARCH003", "ARCH004"}
    }
    assert len(PUBLIC_FACADE_MODULES) == len(set(PUBLIC_FACADE_MODULES))
    assert not facade_errors


def test_transport_neutral_layers_accept_stdlib_and_inward_dependencies(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ancestryllm"
    _write_module(
        root,
        "ancestryllm.domain.models",
        "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Person:\n    id: str\n",
    )
    _write_module(
        root,
        "ancestryllm.application.dto",
        "from ancestryllm.domain.models import Person\n"
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True)\n"
        "class Result:\n"
        "    person: Person\n",
    )

    report = check_tree(
        root,
        public_facades=(),
        exceptions=(),
        require_all_exceptions=False,
        enforce_facades=False,
    )

    assert report.passed


@pytest.mark.parametrize(
    ("dependency", "expected_code"),
    [
        ("import click", "ARCH103"),
        ("from fastapi import FastAPI", "ARCH103"),
        ("from openai import OpenAI", "ARCH103"),
        ("from pydantic import BaseModel", "ARCH103"),
        ("from prompt_toolkit import PromptSession", "ARCH103"),
        ("from rich.console import Console", "ARCH103"),
        ("from electron import BrowserWindow", "ARCH103"),
        ("from pathlib import Path", "ARCH102"),
    ],
)
def test_transport_neutral_layers_reject_frameworks_and_host_objects(
    tmp_path: Path,
    dependency: str,
    expected_code: str,
) -> None:
    root = tmp_path / "ancestryllm"
    _write_module(root, "ancestryllm.application.dto", f"{dependency}\n")

    assert expected_code in _codes(
        root,
        public_facades=(),
        exceptions=(),
        require_all_exceptions=False,
        enforce_facades=False,
    )


@pytest.mark.parametrize(
    "dependency",
    [
        "from ancestryllm.cli import main",
        "from ancestryllm.core.config import AppConfig",
        "from ancestryllm.core.publication import atomic_publish",
        "from ancestryllm.core.secrets import SecretStore",
        "from ancestryllm.llm.providers.openai import OpenAIProvider",
    ],
)
def test_transport_neutral_layers_reject_adapter_and_runtime_implementations(
    tmp_path: Path,
    dependency: str,
) -> None:
    root = tmp_path / "ancestryllm"
    _write_module(root, "ancestryllm.application.dto", f"{dependency}\n")

    assert "ARCH101" in _codes(
        root,
        public_facades=(),
        exceptions=(),
        require_all_exceptions=False,
        enforce_facades=False,
    )


@pytest.mark.parametrize(
    "dependency",
    [
        "from ancestryllm.application import CommandExecutor",
        "from ancestryllm.core.publication import atomic_publish",
        "from ancestryllm.gedcom.engine import write_gedcom",
        "from ancestryllm.rootsmagic.core import RootsMagicReader",
        "from pydantic import BaseModel",
    ],
)
def test_pure_gedcom_document_kernel_rejects_outward_dependencies(
    tmp_path: Path,
    dependency: str,
) -> None:
    root = tmp_path / "ancestryllm"
    _write_module(root, "ancestryllm.gedcom.validator", f"{dependency}\n")

    assert "ARCH101" in _codes(
        root,
        public_facades=(),
        exceptions=(),
        require_all_exceptions=False,
        enforce_facades=False,
    ) or "ARCH103" in _codes(
        root,
        public_facades=(),
        exceptions=(),
        require_all_exceptions=False,
        enforce_facades=False,
    )


@pytest.mark.parametrize(
    "module",
    (
        "ancestryllm.gedcom.graph",
        "ancestryllm.gedcom.identity",
        "ancestryllm.gedcom.quality",
        "ancestryllm.gedcom.sync_kernel",
    ),
)
@pytest.mark.parametrize(
    "dependency",
    [
        "from ancestryllm.cli import main",
        "from ancestryllm.core.config import AppConfig",
        "from ancestryllm.core.publication import atomic_publish",
        "from ancestryllm.core.secrets import SecretStore",
        "from ancestryllm.llm.providers.openai import OpenAIProvider",
        "import httpx",
        "from keyring import get_password",
        "from prompt_toolkit import PromptSession",
    ],
)
def test_gedcom_operations_reject_runtime_and_adapter_dependencies(
    tmp_path: Path,
    module: str,
    dependency: str,
) -> None:
    root = tmp_path / "ancestryllm"
    _write_module(root, module, f"{dependency}\n")

    assert "ARCH104" in _codes(
        root,
        public_facades=(),
        exceptions=(),
        require_all_exceptions=False,
        enforce_facades=False,
    )


def test_private_kernel_import_outside_its_owner_fails_actionably(tmp_path: Path) -> None:
    root = tmp_path / "ancestryllm"
    _write_module(root, "ancestryllm.gedcom.engine", "class GedcomRecord: ...\n")
    _write_module(
        root,
        "ancestryllm.ocr.service",
        "from ancestryllm.gedcom.engine import GedcomRecord\n",
    )

    report = check_tree(
        root,
        public_facades=(),
        exceptions=(),
        require_all_exceptions=False,
        enforce_facades=False,
    )

    assert any(
        violation.code == "ARCH201"
        and "use the public 'ancestryllm.gedcom' façade through a declared gateway"
        in violation.message
        for violation in report.violations
    )


def test_private_kernel_import_from_feature_service_in_same_owner_fails(tmp_path: Path) -> None:
    root = tmp_path / "ancestryllm"
    _write_module(root, "ancestryllm.gedcom.engine", "class GedcomRecord: ...\n")
    _write_module(
        root,
        "ancestryllm.gedcom.service",
        "from ancestryllm.gedcom.engine import GedcomRecord\n",
    )

    report = check_tree(
        root,
        public_facades=(),
        exceptions=(),
        require_all_exceptions=False,
        enforce_facades=False,
    )

    assert any(violation.code == "ARCH201" for violation in report.violations)


def test_importing_an_undeclared_facade_symbol_fails(tmp_path: Path) -> None:
    root = tmp_path / "ancestryllm"
    _write_module(
        root,
        "ancestryllm.application.dto",
        '__all__ = ["PublicResult"]\nclass PublicResult: ...\nclass InternalResult: ...\n',
    )
    _write_module(
        root,
        "ancestryllm.feature",
        "from ancestryllm.application.dto import InternalResult\n",
    )

    assert "ARCH301" in _codes(
        root,
        public_facades=("ancestryllm.application.dto",),
        exceptions=(),
        require_all_exceptions=False,
    )


def test_exception_expansion_is_rejected_and_original_becomes_stale(tmp_path: Path) -> None:
    root = tmp_path / "ancestryllm"
    _write_module(
        root,
        "ancestryllm.cli",
        "from ancestryllm.api.routes import create_app, debug_app\n",
    )
    _write_module(
        root,
        "ancestryllm.api.routes",
        "def create_app(): ...\ndef debug_app(): ...\n",
    )
    exception = DependencyException(
        importer="ancestryllm.cli",
        imported="ancestryllm.api.routes",
        names=("create_app",),
        owner="future API adapter",
        issue="#11",
        reason="Compatibility-only dependency.",
    )

    codes = _codes(
        root,
        public_facades=(),
        exceptions=(exception,),
        enforce_facades=False,
    )

    assert {"ARCH202", "ARCH401"} <= codes


def test_temporary_exceptions_are_exact_owned_and_issue_bound() -> None:
    assert TEMPORARY_EXCEPTIONS == ()


def test_dead_exception_is_a_failure(tmp_path: Path) -> None:
    root = tmp_path / "ancestryllm"
    _write_module(root, "ancestryllm.cli", "def main(): ...\n")
    exception = DependencyException(
        importer="ancestryllm.cli",
        imported="ancestryllm.console.shell",
        names=("run_repl",),
        owner="executor migration",
        issue="#42",
        reason="Compatibility-only dependency.",
    )

    assert "ARCH401" in _codes(
        root,
        public_facades=(),
        exceptions=(exception,),
        enforce_facades=False,
    )
