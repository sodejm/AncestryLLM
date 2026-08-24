"""Tests for the in-code documentation inventory and classification checker.

Verifies that ``scripts/check_code_documentation.py`` correctly classifies files,
rejects unknown extensions and missing documentation, and passes compliant files.
These tests use temporary fixture trees and explicit tracked-file lists so they
are deterministic and do not depend on any checkout-specific state.

See ``docs/CODE_DOCUMENTATION.md`` for the full policy.
"""

from __future__ import annotations

import textwrap
import tomllib
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from scripts.check_code_documentation import (
    NON_COMMENT_FORMAT_MAP,
    check_file_documentation,
    check_inventory,
    classify,
    list_tracked_files,
    main,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "code_documentation"


def _fixture_source(name: str) -> str:
    """Load one checked-in source snippet used by documentation-policy tests."""
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# classify() — extension and path-prefix rules
# ---------------------------------------------------------------------------

_CLASSIFICATION_CASES: dict[str, tuple[str, str]] = {
    "python-source-in-src": ("src/ancestryllm/cli.py", "first-party-code"),
    "python-test-file": ("tests/test_something.py", "first-party-test"),
    "python-script": ("scripts/build_release.py", "first-party-script"),
    "typescript-source": ("desktop/src/main/index.ts", "first-party-code"),
    "typescript-declaration-file": (
        "desktop/src/shared-contract/index.d.ts",
        "first-party-code",
    ),
    "typescript-test": ("desktop/src/main/foo.test.ts", "first-party-code"),
    "tsx-source": ("desktop/src/renderer/App.tsx", "first-party-code"),
    "mjs-desktop-script": ("desktop/scripts/verify-build.mjs", "first-party-script"),
    "mjs-desktop-test": (
        "desktop/scripts/verify-build.test.mjs",
        "first-party-script",
    ),
    "e2e-typescript": ("desktop/e2e/shell.spec.ts", "first-party-test"),
    "shell-script": ("scripts/check_repository_safety.sh", "first-party-script"),
    "swift-script": (
        "scripts/export-apple-signing-identity.swift",
        "first-party-script",
    ),
    "yaml-workflow": (".github/workflows/ci.yml", "first-party-config-exec"),
    "toml-pyproject": ("pyproject.toml", "first-party-config-exec"),
    "graphql-query": (
        "config/release-project-query-v1.graphql",
        "first-party-config-exec",
    ),
    "makefile": ("Makefile", "first-party-config-exec"),
    "dockerfile": ("containers/Dockerfile", "first-party-config-exec"),
    "named-dockerfile": (
        "containers/docs-terminal-capture.Dockerfile",
        "first-party-config-exec",
    ),
    "markdown-doc": ("README.md", "non-code-doc"),
    "docs-markdown": ("docs/CODE_DOCUMENTATION.md", "non-code-doc"),
    "docs-png": ("docs/assets/screenshots/terminal/cli-help.png", "non-code-doc"),
    "license-file": ("LICENSE", "non-code-doc"),
    "json-file": (".github/release-config.json", "non-comment-format"),
    "strict-json-compose": ("containers/compose.yaml", "non-comment-format"),
    "strict-json-compose-local": (
        "containers/compose.local.yaml",
        "non-comment-format",
    ),
    "strict-json-compose-remote": (
        "containers/compose.remote.yaml",
        "non-comment-format",
    ),
    "plist-file": (
        "desktop/resources/entitlements.mac.plist",
        "non-comment-format",
    ),
    "ged-fixture": (
        "tests/fixtures/gedcom_adversarial/sample.ged",
        "test-data-fixture",
    ),
    "uv-lock": ("uv.lock", "generated-vendor"),
    "pnpm-lock": ("pnpm-lock.yaml", "generated-vendor"),
    "reviewed-electron-patch": (
        "desktop/patches/electron@39.8.10.patch",
        "generated-vendor",
    ),
    "node-modules-subtree": (
        "desktop/node_modules/foo/index.js",
        "generated-vendor",
    ),
    "vscode-settings": (".vscode/settings.json", "ide-config"),
    "env-example": (".env.example", "non-comment-format"),
    "gitignore": (".gitignore", "non-code-doc"),
    "manifest-in": ("MANIFEST.in", "first-party-config-exec"),
}


class TestClassify:
    """Unit tests for the file-classification function."""

    @pytest.mark.parametrize("case_id", _CLASSIFICATION_CASES)
    def test_known_path(self, case_id: str) -> None:
        path, expected = _CLASSIFICATION_CASES[case_id]
        assert classify(path) == expected

    def test_unreviewed_patch_raises(self) -> None:
        with pytest.raises(ValueError, match="unclassified"):
            classify("desktop/patches/electron@39.8.11.patch")

    def test_unknown_extension_raises(self) -> None:
        with pytest.raises(ValueError, match="unclassified"):
            classify("src/ancestryllm/foo.xyz")


# ---------------------------------------------------------------------------
# check_inventory() — whole-list diagnostics
# ---------------------------------------------------------------------------


class TestCheckInventory:
    """Tests for the inventory check that runs over all tracked paths."""

    def test_known_extensions_produce_no_diagnostics(self, tmp_path: Path) -> None:
        tracked = [
            "src/foo.py",
            "tests/test_foo.py",
            "scripts/build.py",
            "scripts/build.sh",
            ".github/workflows/ci.yml",
            "README.md",
            "uv.lock",
            "tests/fixtures/sample.ged",
        ]
        # Create stub files so check_file_documentation won't error on open.
        for rel in tracked:
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).touch()

        diagnostics = check_inventory(tracked, tmp_path)
        unknown = [d for d in diagnostics if "unknown-extension" in d]
        assert unknown == [], f"Unexpected unknown-extension diagnostics: {unknown}"

    def test_unknown_extension_fails(self, tmp_path: Path) -> None:
        tracked = ["src/foo.xyz"]
        diagnostics = check_inventory(tracked, tmp_path)
        assert any("unknown-extension" in d for d in diagnostics)
        assert any("src/foo.xyz" in d for d in diagnostics)

    def test_permanent_baseline_is_forbidden(self, tmp_path: Path) -> None:
        baseline = tmp_path / "docs" / "CODE_DOCUMENTATION_BASELINE.txt"
        baseline.parent.mkdir(parents=True)
        baseline.write_text("src/foo.py:missing-module-docstring\n")

        diagnostics = check_inventory(["docs/CODE_DOCUMENTATION_BASELINE.txt"], tmp_path)

        assert diagnostics == ["docs/CODE_DOCUMENTATION_BASELINE.txt:permanent-baseline-forbidden"]

    def test_non_comment_format_without_map_entry_fails(self, tmp_path: Path) -> None:
        tracked = ["some/unmapped.json"]
        diagnostics = check_inventory(tracked, tmp_path)
        assert any("non-comment-format-not-mapped" in d for d in diagnostics)

    def test_non_comment_format_with_map_entry_passes(self, tmp_path: Path) -> None:
        # Use a key that is already in NON_COMMENT_FORMAT_MAP.
        key = next(iter(NON_COMMENT_FORMAT_MAP))
        target = tmp_path / NON_COMMENT_FORMAT_MAP[key]
        target.parent.mkdir(parents=True)
        target.write_text("# Authoritative documentation\n")
        diagnostics = check_inventory([key], tmp_path)
        assert not any("not-mapped" in d for d in diagnostics)

    def test_dependency_audit_allowlist_maps_to_its_review_procedure(self) -> None:
        assert (
            NON_COMMENT_FORMAT_MAP["config/dependency-audit-exclusions.json"]
            == "docs/reference/DEPENDENCY_MAINTENANCE.md"
        )

    @pytest.mark.parametrize(
        "path",
        [
            "config/docs-terminal-capture-policy-v1.schema.json",
            "config/docs-terminal-capture-policy.json",
        ],
    )
    def test_terminal_capture_policy_maps_to_authoring_guidance(self, path: str) -> None:
        assert NON_COMMENT_FORMAT_MAP[path] == "docs/DOCS_AUTHORING.md"

    @pytest.mark.parametrize(
        "path",
        [
            "containers/compose.yaml",
            "containers/compose.local.yaml",
            "containers/compose.remote.yaml",
        ],
    )
    def test_strict_json_compose_maps_to_deployment_contract(self, path: str) -> None:
        assert NON_COMMENT_FORMAT_MAP[path] == "docs/DEPLOYMENT.md"

    def test_security_dependency_policy_maps_to_release_procedure(self) -> None:
        assert (
            NON_COMMENT_FORMAT_MAP["config/version-1-security-policy.json"] == "docs/RELEASING.md"
        )

    def test_macos_runtime_policy_maps_to_deployment_contract(self) -> None:
        assert (
            NON_COMMENT_FORMAT_MAP["desktop/resources/macos-arm64-runtime-policy-v1.json"]
            == "docs/DEPLOYMENT.md"
        )

    def test_non_comment_format_with_missing_map_target_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key = next(iter(NON_COMMENT_FORMAT_MAP))
        monkeypatch.setitem(NON_COMMENT_FORMAT_MAP, key, "docs/missing.md")

        diagnostics = check_inventory([key], tmp_path)

        assert diagnostics == [f"{key}:non-comment-format-map-target-missing"]


class TestTrackedFiles:
    """Tests for the Git-backed working-tree inventory."""

    def test_git_paths_use_nul_delimiters_without_quote_loss(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tracked = ["src/café.py", "src/line\nbreak.py"]
        for relative_path in tracked:
            path = tmp_path / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('"""Documents a machine-safe tracked path."""\n')

        def fake_run(*args: object, **kwargs: object) -> CompletedProcess[str]:
            assert args[0] == ["git", "ls-files", "-z"]
            return CompletedProcess(
                args=["git", "ls-files", "-z"],
                returncode=0,
                stdout="\0".join(tracked) + "\0",
                stderr="",
            )

        monkeypatch.setattr("scripts.check_code_documentation.subprocess.run", fake_run)

        assert list_tracked_files(tmp_path) == tracked

    def test_unstaged_deleted_paths_are_not_checked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kept = tmp_path / "kept.py"
        kept.write_text('"""Documents the retained module."""\n')

        monkeypatch.setattr(
            "scripts.check_code_documentation.subprocess.run",
            lambda *args, **kwargs: CompletedProcess(
                args=["git", "ls-files", "-z"],
                returncode=0,
                stdout="deleted.py\0kept.py\0",
                stderr="",
            ),
        )

        assert list_tracked_files(tmp_path) == ["kept.py"]


# ---------------------------------------------------------------------------
# check_file_documentation() — per-file documentation checks
# ---------------------------------------------------------------------------


class TestCheckFileDocumentation:
    """Tests for the per-file documentation presence check."""

    @pytest.mark.parametrize(
        ("relative_path", "fixture_name"),
        [
            ("src/example.py", "valid_python.txt"),
            ("desktop/src/main/example.ts", "valid_typescript.txt"),
            ("desktop/src/shared-contract/example.d.ts", "valid_typescript_declaration.txt"),
            ("scripts/example.swift", "valid_swift.txt"),
            ("scripts/example.sh", "valid_shell.txt"),
            ("docs/_layouts/example.html", "valid_html.txt"),
            ("desktop/src/renderer/example.css", "valid_css.txt"),
        ],
    )
    def test_checked_in_positive_fixtures_pass(
        self, tmp_path: Path, relative_path: str, fixture_name: str
    ) -> None:
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True)
        target.write_text(_fixture_source(fixture_name), encoding="utf-8")

        assert check_file_documentation(relative_path, tmp_path) == []

    @pytest.mark.parametrize(
        ("relative_path", "fixture_name", "expected_rule"),
        [
            ("src/example.py", "invalid_python_empty.txt", "missing-module-docstring"),
            (
                "desktop/src/main/example.ts",
                "invalid_typescript_plain_comment.txt",
                "missing-file-header-comment",
            ),
            (
                "desktop/src/main/example.ts",
                "invalid_typescript_placeholder.txt",
                "missing-file-header-comment",
            ),
            (
                "scripts/example.swift",
                "invalid_swift_plain_comment.txt",
                "missing-file-header-comment",
            ),
            (
                "docs/_layouts/example.html",
                "invalid_html_empty.txt",
                "missing-file-header-comment",
            ),
            (
                "desktop/src/renderer/example.css",
                "invalid_css_empty.txt",
                "missing-file-header-comment",
            ),
        ],
    )
    def test_checked_in_negative_fixtures_fail_closed(
        self,
        tmp_path: Path,
        relative_path: str,
        fixture_name: str,
        expected_rule: str,
    ) -> None:
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True)
        target.write_text(_fixture_source(fixture_name), encoding="utf-8")

        assert check_file_documentation(relative_path, tmp_path) == [
            f"{relative_path}:{expected_rule}"
        ]

    # --- Python ---

    def test_docstring(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "foo.py"
        f.parent.mkdir(parents=True)
        f.write_text(
            '"""Purpose of this module."""\n\n'
            "def func() -> None:\n"
            '    """Perform the representative public operation."""\n'
        )
        assert check_file_documentation("src/foo.py", tmp_path) == []

    def test_python_without_module_docstring_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "foo.py"
        f.parent.mkdir(parents=True)
        f.write_text("def func() -> None:\n    pass\n")
        diags = check_file_documentation("src/foo.py", tmp_path)
        assert any("missing-module-docstring" in d for d in diags)

    def test_empty_python_file_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "empty.py"
        f.parent.mkdir(parents=True)
        f.write_text("")
        diags = check_file_documentation("src/empty.py", tmp_path)
        assert any("missing-module-docstring" in d for d in diags)

    def test_empty_python_docstring_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "empty_docstring.py"
        f.parent.mkdir(parents=True)
        f.write_text('"""   """\n')

        assert check_file_documentation("src/empty_docstring.py", tmp_path) == [
            "src/empty_docstring.py:missing-module-docstring"
        ]

    def test_python_with_only_comment_fails(self, tmp_path: Path) -> None:
        """A module with only a # comment, not a docstring, must fail."""
        f = tmp_path / "src" / "commented.py"
        f.parent.mkdir(parents=True)
        f.write_text("# This is a comment, not a docstring\ndef func() -> None:\n    pass\n")
        diags = check_file_documentation("src/commented.py", tmp_path)
        assert any("missing-module-docstring" in d for d in diags)

    def test_python_init_with_docstring_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "pkg" / "__init__.py"
        f.parent.mkdir(parents=True)
        f.write_text('"""Package purpose."""\n')
        assert check_file_documentation("src/pkg/__init__.py", tmp_path) == []

    def test_python_main_with_docstring_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "__main__.py"
        f.parent.mkdir(parents=True)
        f.write_text('"""Entry-point module."""\nimport sys\n')
        assert check_file_documentation("src/__main__.py", tmp_path) == []

    def test_python_public_declarations_require_meaningful_docstrings(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "example.py"
        f.parent.mkdir(parents=True)
        f.write_text(_fixture_source("invalid_python_public_declarations.txt"))

        assert check_file_documentation("src/example.py", tmp_path) == [
            "src/example.py:missing-public-class-docstring:PublicService",
            "src/example.py:missing-public-function-docstring:build_result",
            "src/example.py:missing-public-method-docstring:PublicService.execute",
        ]

    def test_python_private_names_exported_through_all_require_docstrings(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "src" / "exported.py"
        f.parent.mkdir(parents=True)
        f.write_text(_fixture_source("invalid_python_exported_declarations.txt"))

        assert check_file_documentation("src/exported.py", tmp_path) == [
            "src/exported.py:missing-public-class-docstring:_ExportedAdapter",
            "src/exported.py:missing-public-function-docstring:_exported_helper",
            "src/exported.py:missing-public-method-docstring:_ExportedAdapter.run",
        ]

    def test_python_placeholder_declaration_docstring_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "scripts" / "example.py"
        f.parent.mkdir(parents=True)
        f.write_text(_fixture_source("invalid_python_placeholder_declaration.txt"))

        assert check_file_documentation("scripts/example.py", tmp_path) == [
            "scripts/example.py:missing-public-function-docstring:main"
        ]

    def test_python_documented_public_declarations_pass(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "example.py"
        f.parent.mkdir(parents=True)
        f.write_text(_fixture_source("valid_python_declarations.txt"))

        assert check_file_documentation("src/example.py", tmp_path) == []

    def test_python_tests_use_descriptive_names_without_per_test_docstrings(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "tests" / "test_example.py"
        f.parent.mkdir(parents=True)
        f.write_text(
            '"""Verifies the example behavior with descriptive test names."""\n\n'
            "class TestPublicBehavior:\n"
            "    def test_returns_the_normalized_value(self) -> None:\n"
            "        assert True\n"
        )

        assert check_file_documentation("tests/test_example.py", tmp_path) == []

    def test_python_protocol_and_abstract_methods_require_contract_docs(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "src" / "contracts.py"
        f.parent.mkdir(parents=True)
        f.write_text(
            '"""Defines callable service boundaries."""\n\n'
            "from abc import ABC, abstractmethod\n"
            "from typing import Protocol\n\n"
            "class Reader(Protocol):\n"
            '    """Defines the source-reading boundary."""\n\n'
            "    def read(self) -> str: ...\n\n"
            "class Worker(ABC):\n"
            '    """Defines the work execution boundary."""\n\n'
            "    @abstractmethod\n"
            "    def execute(self) -> None:\n"
            "        raise NotImplementedError\n"
        )

        assert check_file_documentation("src/contracts.py", tmp_path) == [
            "src/contracts.py:missing-public-method-docstring:Reader.read",
            "src/contracts.py:missing-public-method-docstring:Worker.execute",
        ]

    def test_python_overload_stubs_are_not_given_competing_docstrings(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "overloads.py"
        f.parent.mkdir(parents=True)
        f.write_text(_fixture_source("valid_python_overloads.txt"))

        assert check_file_documentation("src/overloads.py", tmp_path) == []

    def test_python_override_requires_local_contract_documentation(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "overrides.py"
        f.parent.mkdir(parents=True)
        f.write_text(
            '"""Defines an inherited execution contract."""\n\n'
            "from typing import override\n\n"
            "class Parent:\n"
            '    """Provides the base execution contract."""\n\n'
            "    def execute(self) -> None:\n"
            '        """Execute the base operation."""\n\n'
            "class Child(Parent):\n"
            '    """Provides the specialized execution contract."""\n\n'
            "    @override\n"
            "    def execute(self) -> None:\n"
            "        pass\n"
        )

        assert check_file_documentation("src/overrides.py", tmp_path) == [
            "src/overrides.py:missing-public-method-docstring:Child.execute"
        ]

    def test_python_migration_entrypoints_require_operation_docs(self, tmp_path: Path) -> None:
        relative_path = "src/ancestryllm/storage/migrations/versions/revision.py"
        f = tmp_path / relative_path
        f.parent.mkdir(parents=True)
        f.write_text(
            '"""Migrates the persisted schema for the example revision."""\n\n'
            "def upgrade() -> None:\n"
            "    pass\n\n"
            "def downgrade() -> None:\n"
            '    """Reverse the example schema revision."""\n'
        )

        assert check_file_documentation(relative_path, tmp_path) == [
            f"{relative_path}:missing-public-function-docstring:upgrade"
        ]

    # --- Shell ---

    def test_shell_with_header_comment_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "scripts" / "run.sh"
        f.parent.mkdir(parents=True)
        f.write_text("#!/usr/bin/env bash\n# Purpose: runs the build.\nset -e\n")
        assert check_file_documentation("scripts/run.sh", tmp_path) == []

    def test_shell_without_header_comment_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "scripts" / "noheader.sh"
        f.parent.mkdir(parents=True)
        f.write_text("set -e\necho hello\n")
        diags = check_file_documentation("scripts/noheader.sh", tmp_path)
        assert any("missing-file-header-comment" in d for d in diags)

    def test_shell_empty_comment_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "scripts" / "empty-header.sh"
        f.parent.mkdir(parents=True)
        f.write_text("#!/usr/bin/env bash\n#\nset -e\n")

        assert check_file_documentation("scripts/empty-header.sh", tmp_path) == [
            "scripts/empty-header.sh:missing-file-header-comment"
        ]

    def test_shell_multiline_license_without_separate_purpose_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "scripts" / "licensed.sh"
        f.parent.mkdir(parents=True)
        f.write_text(
            "#!/usr/bin/env bash\n"
            "# Copyright 2026 Example\n"
            "# Permission is hereby granted under the project license.\n"
            "set -e\n"
        )

        assert check_file_documentation("scripts/licensed.sh", tmp_path) == [
            "scripts/licensed.sh:missing-file-header-comment"
        ]

    def test_shell_multiline_license_may_precede_separate_purpose(self, tmp_path: Path) -> None:
        f = tmp_path / "scripts" / "licensed.sh"
        f.parent.mkdir(parents=True)
        f.write_text(
            "#!/usr/bin/env bash\n"
            "# Copyright 2026 Example\n"
            "# Permission is hereby granted under the project license.\n"
            "\n"
            "# Runs the reviewed repository build workflow.\n"
            "set -e\n"
        )

        assert check_file_documentation("scripts/licensed.sh", tmp_path) == []

    # --- TypeScript ---

    def test_typescript_with_jsdoc_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "desktop" / "src" / "main" / "index.ts"
        f.parent.mkdir(parents=True)
        f.write_text(
            textwrap.dedent("""\
            /**
             * Entry point for the Electron main process.
             */
            import { app } from 'electron';
            """)
        )
        assert check_file_documentation("desktop/src/main/index.ts", tmp_path) == []

    def test_typescript_without_header_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "desktop" / "src" / "main" / "bare.ts"
        f.parent.mkdir(parents=True)
        f.write_text("import { app } from 'electron';\n")
        diags = check_file_documentation("desktop/src/main/bare.ts", tmp_path)
        assert any("missing-file-header-comment" in d for d in diags)

    @pytest.mark.parametrize(
        "header",
        [
            "// Plain comments are not module documentation.\n",
            "/** */\n",
            "/** TODO */\n",
            "/** TODO: add documentation later. */\n",
        ],
    )
    def test_typescript_requires_meaningful_jsdoc_header(self, tmp_path: Path, header: str) -> None:
        f = tmp_path / "desktop" / "src" / "main" / "bare.ts"
        f.parent.mkdir(parents=True)
        f.write_text(f"{header}export const value = 1\n")

        assert check_file_documentation("desktop/src/main/bare.ts", tmp_path) == [
            "desktop/src/main/bare.ts:missing-file-header-comment"
        ]

    def test_typescript_license_may_precede_jsdoc_header(self, tmp_path: Path) -> None:
        f = tmp_path / "desktop" / "src" / "main" / "licensed.ts"
        f.parent.mkdir(parents=True)
        f.write_text(
            "/* SPDX-License-Identifier: MIT */\n"
            "/** Defines the licensed desktop module contract. */\n"
            "export const value = 1\n"
        )

        assert check_file_documentation("desktop/src/main/licensed.ts", tmp_path) == []

    def test_typescript_license_without_jsdoc_header_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "desktop" / "src" / "main" / "licensed.ts"
        f.parent.mkdir(parents=True)
        f.write_text("/* SPDX-License-Identifier: MIT */\nexport const value = 1\n")

        assert check_file_documentation("desktop/src/main/licensed.ts", tmp_path) == [
            "desktop/src/main/licensed.ts:missing-file-header-comment"
        ]

    def test_typescript_declaration_file_with_jsdoc_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "desktop" / "src" / "shared-contract" / "index.d.ts"
        f.parent.mkdir(parents=True)
        f.write_text("/** Defines the desktop ambient contract. */\nexport interface Bridge {}\n")

        assert check_file_documentation("desktop/src/shared-contract/index.d.ts", tmp_path) == []

    def test_late_header_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "desktop" / "src" / "main" / "late.ts"
        f.parent.mkdir(parents=True)
        f.write_text("export const version = '0.5.0';\n/* Late comment. */\n")

        diags = check_file_documentation("desktop/src/main/late.ts", tmp_path)

        assert diags == ["desktop/src/main/late.ts:missing-file-header-comment"]

    # --- YAML ---

    def test_yaml_with_header_passes(self, tmp_path: Path) -> None:
        f = tmp_path / ".github" / "workflows" / "ci.yml"
        f.parent.mkdir(parents=True)
        f.write_text("# Continuous integration workflow.\nname: CI\n")
        assert check_file_documentation(".github/workflows/ci.yml", tmp_path) == []

    def test_yaml_without_header_fails(self, tmp_path: Path) -> None:
        f = tmp_path / ".github" / "workflows" / "noheader.yml"
        f.parent.mkdir(parents=True)
        f.write_text("name: CI\n")
        diags = check_file_documentation(".github/workflows/noheader.yml", tmp_path)
        assert any("missing-file-header-comment" in d for d in diags)

    def test_dockerfile_with_header_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "containers" / "Dockerfile"
        f.parent.mkdir(parents=True)
        f.write_text("# Builds the OCI runtime image.\nFROM scratch\n")
        assert check_file_documentation("containers/Dockerfile", tmp_path) == []

    def test_swift_requires_doc_comment(self, tmp_path: Path) -> None:
        f = tmp_path / "scripts" / "helper.swift"
        f.parent.mkdir(parents=True)
        f.write_text("// Plain Swift comment.\nimport Foundation\n")

        assert check_file_documentation("scripts/helper.swift", tmp_path) == [
            "scripts/helper.swift:missing-file-header-comment"
        ]

    def test_swift_doc_comment_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "scripts" / "helper.swift"
        f.parent.mkdir(parents=True)
        f.write_text("/// Exports the reviewed Apple signing identity.\nimport Foundation\n")

        assert check_file_documentation("scripts/helper.swift", tmp_path) == []

    def test_swift_callable_requires_docc(self, tmp_path: Path) -> None:
        f = tmp_path / "scripts" / "helper.swift"
        f.parent.mkdir(parents=True)
        f.write_text(
            "/// Exports the reviewed Apple signing identity.\n"
            "import Foundation\n\n"
            "private func fail(_ message: String) -> Never {\n"
            "    fatalError(message)\n"
            "}\n"
        )

        assert check_file_documentation("scripts/helper.swift", tmp_path) == [
            "scripts/helper.swift:missing-swift-callable-documentation:fail"
        ]

    def test_swift_callable_rejects_placeholder_docc(self, tmp_path: Path) -> None:
        f = tmp_path / "scripts" / "helper.swift"
        f.parent.mkdir(parents=True)
        f.write_text(
            "/// Exports the reviewed Apple signing identity.\n"
            "import Foundation\n\n"
            "/// TODO: document this helper.\n"
            "private func fail(_ message: String) -> Never {\n"
            "    fatalError(message)\n"
            "}\n"
        )

        assert check_file_documentation("scripts/helper.swift", tmp_path) == [
            "scripts/helper.swift:placeholder-swift-callable-documentation:fail"
        ]

    def test_swift_callable_docc_covers_attributes_initializers_and_subscripts(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "scripts" / "helper.swift"
        f.parent.mkdir(parents=True)
        f.write_text(
            "/// Defines documented Swift callable examples.\n"
            "import Foundation\n\n"
            "/// Returns the supplied value for release-script validation.\n"
            "@discardableResult\n"
            "private func checked(_ value: Int) -> Int { value }\n\n"
            "private struct Values {\n"
            "    /// Creates an empty value collection.\n"
            "    init() {}\n\n"
            "    /// Returns the deterministic value at the requested index.\n"
            "    subscript(index: Int) -> Int { index }\n"
            "}\n"
        )

        assert check_file_documentation("scripts/helper.swift", tmp_path) == []

    @pytest.mark.parametrize(
        ("relative_path", "source"),
        [
            (
                "docs/_layouts/documentation.html",
                "<!-- Renders the documentation navigation layout. -->\n<html></html>\n",
            ),
            (
                "desktop/src/renderer/src/styles.css",
                "/* Defines the renderer theme and accessible layout. */\n:root {}\n",
            ),
        ],
    )
    def test_markup_and_stylesheet_headers_pass(
        self, tmp_path: Path, relative_path: str, source: str
    ) -> None:
        f = tmp_path / relative_path
        f.parent.mkdir(parents=True)
        f.write_text(source)

        assert check_file_documentation(relative_path, tmp_path) == []

    @pytest.mark.parametrize(
        ("relative_path", "source"),
        [
            ("docs/_layouts/documentation.html", "<!-- -->\n<html></html>\n"),
            ("desktop/src/renderer/src/styles.css", "/**/\n:root {}\n"),
        ],
    )
    def test_empty_markup_and_stylesheet_headers_fail(
        self, tmp_path: Path, relative_path: str, source: str
    ) -> None:
        f = tmp_path / relative_path
        f.parent.mkdir(parents=True)
        f.write_text(source)

        assert check_file_documentation(relative_path, tmp_path) == [
            f"{relative_path}:missing-file-header-comment"
        ]

    # --- Non-code classifications are skipped ---

    def test_markdown_file_not_checked(self, tmp_path: Path) -> None:
        f = tmp_path / "README.md"
        f.write_text("# Project\n")
        assert check_file_documentation("README.md", tmp_path) == []

    def test_json_file_not_checked(self, tmp_path: Path) -> None:
        f = tmp_path / ".github" / "release-config.json"
        f.parent.mkdir(parents=True)
        f.write_text("{}")
        assert check_file_documentation(".github/release-config.json", tmp_path) == []

    def test_lock_file_not_checked(self, tmp_path: Path) -> None:
        f = tmp_path / "uv.lock"
        f.write_text("")
        assert check_file_documentation("uv.lock", tmp_path) == []


# ---------------------------------------------------------------------------
# main() integration — exit codes and output
# ---------------------------------------------------------------------------


class TestMain:
    """Integration tests for the CLI entry point."""

    def test_all_clean_exits_zero(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "foo.py"
        f.parent.mkdir(parents=True)
        f.write_text('"""Module purpose."""\n')

        code = main(["--root", str(tmp_path), "--tracked-files", "src/foo.py"])
        assert code == 0

    def test_missing_docstring_exits_one(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "foo.py"
        f.parent.mkdir(parents=True)
        f.write_text("def func(): pass\n")

        code = main(["--root", str(tmp_path), "--tracked-files", "src/foo.py"])
        assert code == 1

    def test_unknown_extension_exits_one(self, tmp_path: Path) -> None:
        code = main(["--root", str(tmp_path), "--tracked-files", "src/foo.xyz"])
        assert code == 1

    def test_empty_tracked_list_exits_zero(self, tmp_path: Path) -> None:
        code = main(["--root", str(tmp_path), "--tracked-files"])
        assert code == 0

    def test_baseline_argument_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--root", str(tmp_path), "--baseline", "baseline.txt"])

        assert exc_info.value.code == 2


def test_python_declaration_rules_are_explicit_and_test_exceptions_are_narrow() -> None:
    root = Path(__file__).resolve().parents[1]
    configuration = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    lint = configuration["tool"]["ruff"]["lint"]

    documentation_rules = {"D100", "D101", "D102", "D103", "D104", "D418", "D419"}
    assert documentation_rules <= set(lint["select"])
    assert lint["per-file-ignores"]["tests/**"] == [
        "D101",
        "D102",
        "D103",
        "RUF003",
        "RUF059",
        "S105",
        "S106",
        "S108",
        "S603",
        "S607",
    ]
    assert all(
        not rule.startswith("D")
        for pattern, rules in lint["per-file-ignores"].items()
        if pattern != "tests/**"
        for rule in rules
    )
