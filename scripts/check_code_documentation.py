#!/usr/bin/env python3
"""In-code documentation inventory and classification checker.

Classifies every Git-tracked file in the repository into exactly one category and
verifies that every first-party source file has a module/file-level purpose statement.
Rejects unknown file extensions in comment-capable categories.

Invocation::

    python scripts/check_code_documentation.py [--root <repo-root>]

Exits 0 when all checks pass, 1 on any violation. Emits stable ``path:rule``
diagnostics to stdout so output is diffable in CI logs.

This script is offline and deterministic; it does not call any provider, upload
source, or require credentials. See ``docs/CODE_DOCUMENTATION.md`` for the
full policy.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Classification tables
# ---------------------------------------------------------------------------

# Extensions whose files carry executable/behaviour-defining configuration or
# first-party code and therefore require file-level documentation.
FIRST_PARTY_CODE_EXTENSIONS: Final = frozenset(
    {".py", ".ts", ".tsx", ".js", ".mjs", ".sh", ".swift", ".html", ".css"}
)

# Makefile and comment-capable config formats that require file-level purpose comments.
EXEC_CONFIG_EXTENSIONS: Final = frozenset({".yml", ".yaml", ".toml"})

# Extensions that identify generated/vendored or lock files — excluded from
# documentation requirements but must be explicitly classified.
GENERATED_VENDOR_NAMES: Final = frozenset({"uv.lock", "pnpm-lock.yaml"})
GENERATED_VENDOR_EXTENSIONS: Final = frozenset({".lock"})

# Extensions that identify test-data fixtures — excluded from documentation
# requirements but must be classified.
TEST_DATA_FIXTURE_EXTENSIONS: Final = frozenset({".ged", ".gedcom", ".gitkeep"})

# Extensions for non-code human-readable documentation.
NON_CODE_DOC_EXTENSIONS: Final = frozenset({".md", ".rst", ".txt"})

# Formats that do not safely permit comments.  Their semantics must be
# explained in NON_COMMENT_FORMAT_MAP below.
NON_COMMENT_FORMAT_EXTENSIONS: Final = frozenset({".json", ".plist", ".xml"})

# Editor/IDE configuration that does not need documentation.
IDE_CONFIG_DIRS: Final = frozenset({".vscode", ".idea"})

# Whole-name overrides that override the extension-based rules above.
GENERATED_VENDOR_BASENAMES: Final = frozenset({"uv.lock", "pnpm-lock.yaml", "components.json"})

# Special basenames that have no extension but are classified.
EXTENSIONLESS_BASENAMES: Final = {
    "Makefile": "first-party-config-exec",
    "LICENSE": "non-code-doc",
    "MANIFEST.in": "first-party-config-exec",
    ".gitignore": "non-code-doc",
    ".gitattributes": "non-code-doc",
    ".pre-commit-config.yaml": "first-party-config-exec",
    ".node-version": "generated-vendor",
}

# Pseudo-extensions for files with compound names or known special cases.
KNOWN_SPECIAL_EXTENSIONS: Final = frozenset({".example", ".in"})

# Non-comment JSON/plist files mapped to their adjacent authoritative document.
NON_COMMENT_FORMAT_MAP: Final[dict[str, str]] = {
    # GitHub release config — semantics described in docs/RELEASING.md
    ".github/release-config.json": "docs/RELEASING.md",
    # Issue/PR template form schemas — self-describing YAML siblings in same dir
    ".github/ISSUE_TEMPLATE/config.yml": "docs/CI.md",
    # desktop components.json — shadcn/ui generated registry; see desktop/README.md
    "desktop/components.json": "desktop/README.md",
    # plist/XML: Electron builder configuration; see docs/DESKTOP_SIDECAR.md
    "desktop/resources/entitlements.mac.plist": "docs/DESKTOP_SIDECAR.md",
    # .env.example — example environment variable names; see README.md for setup
    ".env.example": "README.md",
    # desktop/package.json — Node package manifest; semantics in desktop/README.md
    "desktop/package.json": "desktop/README.md",
    # TypeScript project references — semantics in desktop/README.md
    "desktop/tsconfig.json": "desktop/README.md",
    "desktop/tsconfig.base.json": "desktop/README.md",
    "desktop/tsconfig.main.json": "desktop/README.md",
    "desktop/tsconfig.preload.json": "desktop/README.md",
    "desktop/tsconfig.renderer.json": "desktop/README.md",
    "desktop/tsconfig.shared.json": "desktop/README.md",
    "desktop/tsconfig.test.json": "desktop/README.md",
    # Characterization baseline — generated snapshot; see tests/test_core_contracts_characterization.py
    "tests/characterization/core_contracts_0_3_baseline.json": "tests/test_core_contracts_characterization.py",
    # Test fixture manifest — describes adversarial GEDCOM set; see tests/test_gedcom_adversarial.py
    "tests/fixtures/gedcom_adversarial/manifest.json": "tests/test_gedcom_adversarial.py",
    # Jekyll page metadata; see the 0.6.0 documentation release notes.
    "docs/_data/page_metadata.json": "docs/release-notes/0.6.0.md",
    # External-link exceptions and their operating policy.
    "docs/_data/external_link_exceptions.json": "docs/WIKI_OPERATIONS.md",
    # Executable bootstrap trust roots and their reviewed update procedure.
    "config/uv-bootstrap-policy.json": "docs/security/verified-uv-bootstrap.md",
}

# ---------------------------------------------------------------------------
# Path-prefix exclusion rules for generated/vendored subtrees
# ---------------------------------------------------------------------------

GENERATED_VENDOR_PATH_PREFIXES: Final = (
    "desktop/node_modules/",
    "desktop/dist/",
    "desktop/out/",
    ".venv/",
    "dist/",
    "build/",
    # Release evidence JSON and OpenAPI spec are generated output, not source.
    "docs/release-evidence/",
    "docs/api/",
)

# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------


def _is_generated_vendor(rel: str) -> bool:
    """Return True when *rel* identifies a generated or vendored file."""
    if any(rel.startswith(p) for p in GENERATED_VENDOR_PATH_PREFIXES):
        return True
    basename = Path(rel).name
    if basename in GENERATED_VENDOR_BASENAMES:
        return True
    suffix = Path(rel).suffix.lower()
    return suffix in GENERATED_VENDOR_EXTENSIONS


def classify(rel: str) -> str:
    """Return the single classification label for a Git-tracked path *rel*.

    Raises ``ValueError`` when the path cannot be classified, which the caller
    should surface as a ``path:unknown-extension`` diagnostic.
    """
    path = Path(rel)
    name = path.name
    suffix = path.suffix.lower()
    parts = path.parts

    # Generated/vendor subtrees — checked before extension rules.
    if _is_generated_vendor(rel):
        return "generated-vendor"

    # IDE config directories.
    if parts[0] in IDE_CONFIG_DIRS or (len(parts) > 1 and parts[1] in IDE_CONFIG_DIRS):
        return "ide-config"

    # Extensionless known basenames.
    if name in EXTENSIONLESS_BASENAMES:
        return EXTENSIONLESS_BASENAMES[name]
    # Dotfiles without a meaningful suffix (e.g. .gitkeep already covered by extension).
    if not suffix and name.startswith("."):
        return "non-code-doc"

    # Test-data fixtures (before code extensions so .ged wins over any overlap).
    if suffix in TEST_DATA_FIXTURE_EXTENSIONS:
        return "test-data-fixture"

    # Non-comment formats.
    if suffix in NON_COMMENT_FORMAT_EXTENSIONS:
        return "non-comment-format"

    # Non-code documentation.
    if suffix in NON_CODE_DOC_EXTENSIONS:
        return "non-code-doc"

    # Special known extensions that are not comment-capable.
    if suffix in KNOWN_SPECIAL_EXTENSIONS:
        return "non-comment-format"

    # Makefile/executable config.
    if name == "Makefile" or suffix in EXEC_CONFIG_EXTENSIONS:
        return "first-party-config-exec"

    # First-party code: further split into code, test, and script sub-types.
    if suffix in FIRST_PARTY_CODE_EXTENSIONS:
        if rel.startswith("tests/") or rel.startswith("desktop/e2e/"):
            return "first-party-test"
        if rel.startswith("scripts/") or rel.startswith("desktop/scripts/"):
            return "first-party-script"
        return "first-party-code"

    raise ValueError(f"unclassified: {rel!r}")


# ---------------------------------------------------------------------------
# Module/file-level documentation checkers
# ---------------------------------------------------------------------------


def _has_python_module_docstring(path: Path) -> bool:
    """Return True when the Python source at *path* has a module-level docstring."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    # ast.get_docstring returns None when there is no docstring.
    return ast.get_docstring(tree) is not None


_SHEBANG_RE = re.compile(r"^#!")

# Pattern for a standalone-line comment (shell/Python/YAML/TOML/Makefile/TS/Swift).
_SINGLE_LINE_COMMENT_RE = re.compile(r"^\s*(?:\#[^\n]*|//[^\n]*|///[^\n]*)")


def _has_file_level_comment(path: Path) -> bool:
    """Return True when a text file at *path* has a recognisable file-level comment.

    Checks for any comment-like token in the first meaningful content of the file.
    A header may follow blank lines and a shebang, but must precede source code.
    Human review confirms the comment is genuinely useful.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    lines = text.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _SHEBANG_RE.match(stripped):
            continue
        # Single-line comment immediately at the start.
        if _SINGLE_LINE_COMMENT_RE.match(stripped):
            return True
        if stripped.startswith(("/*", "<!--")):
            return True
        # Non-empty, non-shebang content without a comment means no file header.
        break
    return False


# ---------------------------------------------------------------------------
# Per-classification check dispatch
# ---------------------------------------------------------------------------

# Classifications that require a file-level documentation check.
DOCUMENTED_CLASSIFICATIONS: Final = frozenset(
    {
        "first-party-code",
        "first-party-test",
        "first-party-script",
        "first-party-config-exec",
    }
)

# File extensions that use Python AST docstring detection.
PYTHON_EXTENSIONS: Final = frozenset({".py"})


def check_file_documentation(rel: str, root: Path) -> list[str]:
    """Return a list of diagnostic strings for *rel*, or an empty list when ok.

    Each diagnostic has the form ``rel:rule`` so output is stable and greppable.
    """
    path = root / rel
    classification = classify(rel)

    if classification not in DOCUMENTED_CLASSIFICATIONS:
        return []

    suffix = Path(rel).suffix.lower()

    if suffix in PYTHON_EXTENSIONS:
        if not _has_python_module_docstring(path):
            return [f"{rel}:missing-module-docstring"]
    else:
        if not _has_file_level_comment(path):
            return [f"{rel}:missing-file-header-comment"]

    return []


# ---------------------------------------------------------------------------
# Inventory check — every path must classify without error
# ---------------------------------------------------------------------------


def check_inventory(tracked: list[str], root: Path) -> list[str]:
    """Classify every tracked path and return diagnostics for unknown extensions.

    Also validates that non-comment-format files have an entry in
    ``NON_COMMENT_FORMAT_MAP`` whose target exists, so their semantics are
    discoverable.
    """
    diagnostics: list[str] = []
    for rel in tracked:
        try:
            classification = classify(rel)
        except ValueError:
            diagnostics.append(f"{rel}:unknown-extension")
            continue

        if classification == "non-comment-format":
            documentation_path = NON_COMMENT_FORMAT_MAP.get(rel)
            if documentation_path is None:
                diagnostics.append(f"{rel}:non-comment-format-not-mapped")
            elif not (root / documentation_path).is_file():
                diagnostics.append(f"{rel}:non-comment-format-map-target-missing")

    return diagnostics


# ---------------------------------------------------------------------------
# Git integration
# ---------------------------------------------------------------------------


def list_tracked_files(root: Path) -> list[str]:
    """Return all Git-tracked relative paths under *root*."""
    result = subprocess.run(
        ["git", "ls-files"],  # noqa: S607 — git is a well-known system executable
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def load_baseline(path: Path | None) -> set[str]:
    """Return approved legacy diagnostics recorded in *path*, if it exists."""
    if path is None or not path.is_file():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the inventory and documentation checks; return an exit code.

    Returns 0 when all checks pass, 1 on any violation.
    """
    parser = argparse.ArgumentParser(
        description="Check in-code documentation coverage across Git-tracked files."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (default: parent of this script's directory).",
    )
    parser.add_argument(
        "--tracked-files",
        nargs="*",
        default=None,
        help="Explicit list of tracked paths (overrides git ls-files; for testing).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "Approved legacy diagnostics to exclude. Defaults to "
            "docs/CODE_DOCUMENTATION_BASELINE.txt when that file exists."
        ),
    )
    args = parser.parse_args(argv)
    root: Path = args.root
    baseline_path = args.baseline
    if baseline_path is None:
        baseline_path = root / "docs" / "CODE_DOCUMENTATION_BASELINE.txt"

    tracked: list[str] = (
        args.tracked_files if args.tracked_files is not None else list_tracked_files(root)
    )

    diagnostics: list[str] = []

    # 1. Inventory: every path must classify; non-comment formats must be mapped.
    diagnostics.extend(check_inventory(tracked, root))

    # 2. Documentation: every in-scope file must have a file-level statement.
    for rel in tracked:
        with suppress(ValueError):
            diagnostics.extend(check_file_documentation(rel, root))
            # Already captured as unknown-extension above.

    baseline = load_baseline(baseline_path)
    diagnostics = [diag for diag in diagnostics if diag not in baseline]

    if diagnostics:
        for diag in sorted(diagnostics):
            print(diag)
        print(
            f"\ncheck_code_documentation: {len(diagnostics)} violation(s) found.",
            file=sys.stderr,
        )
        return 1

    print("check_code_documentation: all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
