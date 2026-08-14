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
EXEC_CONFIG_EXTENSIONS: Final = frozenset({".dockerfile", ".graphql", ".yml", ".yaml", ".toml"})

# Extensions that identify generated/vendored or lock files — excluded from
# documentation requirements but must be explicitly classified.
GENERATED_VENDOR_NAMES: Final = frozenset({"uv.lock", "pnpm-lock.yaml"})
GENERATED_VENDOR_EXTENSIONS: Final = frozenset({".lock"})

# Exact reviewed vendor patch inputs. Patch syntax has no portable file-header
# comment form, so each artifact is allowlisted by path instead of admitting all
# ``.patch`` files. Its behavior and integrity pin are documented beside the
# desktop dependency-install contract.
GENERATED_VENDOR_PATHS: Final = frozenset({"desktop/patches/electron@39.8.10.patch"})

# Extensions that identify test-data fixtures — excluded from documentation
# requirements but must be classified.
TEST_DATA_FIXTURE_EXTENSIONS: Final = frozenset({".ged", ".gedcom", ".gitkeep"})

# Extensions for non-code human-readable documentation.
NON_CODE_DOC_EXTENSIONS: Final = frozenset({".md", ".png", ".rst", ".txt"})

# Formats that do not safely permit comments.  Their semantics must be
# explained in NON_COMMENT_FORMAT_MAP below.
NON_COMMENT_FORMAT_EXTENSIONS: Final = frozenset({".json", ".plist", ".xml"})

# Files whose suffix ordinarily permits comments but whose stricter parser contract
# intentionally does not. These Compose manifests remain strict JSON so duplicate
# keys and non-JSON YAML constructs fail closed.
NON_COMMENT_FORMAT_PATHS: Final = frozenset(
    {
        "containers/compose.yaml",
        "containers/compose.local.yaml",
        "containers/compose.remote.yaml",
    }
)

# Editor/IDE configuration that does not need documentation.
IDE_CONFIG_DIRS: Final = frozenset({".vscode", ".idea"})

# Whole-name overrides that override the extension-based rules above.
GENERATED_VENDOR_BASENAMES: Final = frozenset({"uv.lock", "pnpm-lock.yaml", "components.json"})

# Special basenames that have no extension but are classified.
EXTENSIONLESS_BASENAMES: Final = {
    "Dockerfile": "first-party-config-exec",
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
    # Strict-JSON Compose topology and reviewed deployment profiles.
    "containers/compose.yaml": "docs/DEPLOYMENT.md",
    "containers/compose.local.yaml": "docs/DEPLOYMENT.md",
    "containers/compose.remote.yaml": "docs/DEPLOYMENT.md",
    # GitHub release config — semantics described in docs/RELEASING.md
    ".github/release-config.json": "docs/RELEASING.md",
    # Issue/PR template form schemas — self-describing YAML siblings in same dir
    ".github/ISSUE_TEMPLATE/config.yml": "docs/reference/CI.md",
    # desktop components.json — shadcn/ui generated registry; see desktop/README.md
    "desktop/components.json": "desktop/README.md",
    # plist/XML: Electron builder configuration; see docs/reference/DESKTOP_SIDECAR.md
    "desktop/resources/entitlements.mac.plist": "docs/reference/DESKTOP_SIDECAR.md",
    # .env.example — example environment variable names; see README.md for setup
    ".env.example": "README.md",
    # desktop/package.json — Node package manifest; semantics in desktop/README.md
    "desktop/package.json": "desktop/README.md",
    # macOS ARM64 executable trust policy and reviewed lifecycle contract.
    "desktop/resources/macos-arm64-runtime-policy-v1.json": "docs/DEPLOYMENT.md",
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
    # Dependency-audit export exclusions and their fail-closed review procedure.
    "config/dependency-audit-exclusions.json": "docs/reference/DEPENDENCY_MAINTENANCE.md",
    # Version 1 security dependency policy and its reviewed update procedure.
    "config/version-1-security-policy.json": "docs/RELEASING.md",
    # Deterministic screenshot contract and its fictional fixture inputs.
    "config/docs-screenshot-fixture-v1.schema.json": "docs/DOCS_AUTHORING.md",
    "config/docs-screenshot-manifest-v1.schema.json": "docs/DOCS_AUTHORING.md",
    "config/docs-screenshot-manifest.json": "docs/DOCS_AUTHORING.md",
    "config/docs-screenshot-drift-report-v1.schema.json": "docs/DOCS_AUTHORING.md",
    "config/docs-terminal-capture-policy-v1.schema.json": "docs/DOCS_AUTHORING.md",
    "config/docs-terminal-capture-policy.json": "docs/DOCS_AUTHORING.md",
    "scripts/docs_screenshots.py": "docs/DOCS_AUTHORING.md",
    "tests/fixtures/docs_screenshots/degraded.json": "docs/DOCS_AUTHORING.md",
    "tests/fixtures/docs_screenshots/electron-degraded.json": "docs/DOCS_AUTHORING.md",
    "tests/fixtures/docs_screenshots/privacy-canary.json": "docs/DOCS_AUTHORING.md",
    "tests/fixtures/docs_screenshots/success.json": "docs/DOCS_AUTHORING.md",
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
    if rel in GENERATED_VENDOR_PATHS:
        return True
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
    if rel in NON_COMMENT_FORMAT_PATHS or suffix in NON_COMMENT_FORMAT_EXTENSIONS:
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

PERMANENT_BASELINE_PATH: Final = "docs/CODE_DOCUMENTATION_BASELINE.txt"
_PLACEHOLDER_DOCUMENTATION_WORDS: Final = frozenset(
    {"doc", "docs", "documentation", "fixme", "placeholder", "tbd", "todo"}
)
_PLACEHOLDER_MARKER_WORDS: Final = frozenset({"fixme", "placeholder", "tbd", "todo"})
_LICENSE_MARKERS: Final = ("copyright", "spdx-license-identifier", "licensed under")
_SWIFT_ATTRIBUTE_LINE: Final = re.compile(r"^\s*@[A-Za-z_][A-Za-z0-9_.]*(?:\([^)]*\))?\s*$")
_SWIFT_CALLABLE_DECLARATION: Final = re.compile(
    r"""(?x)
    ^\s*
    (?:(?:
        @[A-Za-z_][A-Za-z0-9_.]*(?:\([^)]*\))?
        | public | private | fileprivate | internal | open
        | static | class | final | override | required | convenience
        | mutating | nonmutating | nonisolated | distributed
        | borrowing | consuming | prefix | postfix | infix
    )\s+)*
    (?:
        func\s+(?P<function>`?[A-Za-z_][A-Za-z0-9_]*`?|[+\-*/%=!<>?&|^~]+)
        | (?P<initializer>init[!?]?)\s*\(
        | (?P<subscript>subscript)\s*(?:<[^>]*>\s*)?\(
    )
    """
)


def _is_overload_declaration(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return whether *node* is an overload signature rather than its implementation."""
    return any(
        (isinstance(decorator, ast.Name) and decorator.id == "overload")
        or (isinstance(decorator, ast.Attribute) and decorator.attr == "overload")
        for decorator in node.decorator_list
    )


def _has_meaningful_python_docstring(
    node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Return whether *node* owns a meaningful Python docstring."""
    docstring = ast.get_docstring(node, clean=False)
    return docstring is not None and _is_meaningful_documentation(docstring)


def _is_public_method_name(name: str) -> bool:
    """Return whether *name* identifies a documented public method contract."""
    return not name.startswith("_") or name == "__call__"


def _literal_python_exports(tree: ast.Module) -> frozenset[str]:
    """Return string names declared by a top-level literal ``__all__`` assignment."""
    exports: set[str] = set()
    for statement in tree.body:
        value: ast.expr | None = None
        if (
            isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in statement.targets
            )
        ) or (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "__all__"
        ):
            value = statement.value

        if value is None:
            continue
        try:
            literal = ast.literal_eval(value)
        except (TypeError, ValueError):
            continue
        if isinstance(literal, (list, tuple, set, frozenset)) and all(
            isinstance(name, str) for name in literal
        ):
            exports.update(literal)
    return frozenset(exports)


def _check_public_class_documentation(
    rel: str,
    node: ast.ClassDef,
    *,
    parent_name: str | None = None,
) -> list[str]:
    """Return declaration diagnostics for one public class and its public members."""
    qualified_name = f"{parent_name}.{node.name}" if parent_name else node.name
    diagnostics: list[str] = []
    if not _has_meaningful_python_docstring(node):
        diagnostics.append(f"{rel}:missing-public-class-docstring:{qualified_name}")

    for member in node.body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                _is_public_method_name(member.name)
                and not _is_overload_declaration(member)
                and not _has_meaningful_python_docstring(member)
            ):
                diagnostics.append(
                    f"{rel}:missing-public-method-docstring:{qualified_name}.{member.name}"
                )
        elif isinstance(member, ast.ClassDef) and not member.name.startswith("_"):
            diagnostics.extend(
                _check_public_class_documentation(
                    rel,
                    member,
                    parent_name=qualified_name,
                )
            )

    return diagnostics


def _check_python_documentation(
    rel: str,
    path: Path,
    *,
    check_public_declarations: bool,
) -> list[str]:
    """Return module and public-declaration diagnostics for one Python source file."""
    source = _read_source(path)
    if source is None:
        return [f"{rel}:missing-module-docstring"]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [f"{rel}:missing-module-docstring"]

    diagnostics: list[str] = []
    if not _has_meaningful_python_docstring(tree):
        diagnostics.append(f"{rel}:missing-module-docstring")

    if check_public_declarations:
        exports = _literal_python_exports(tree)
        for declaration in tree.body:
            if not isinstance(
                declaration,
                (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                continue
            is_public = not declaration.name.startswith("_") or declaration.name in exports
            if isinstance(declaration, ast.ClassDef) and is_public:
                diagnostics.extend(_check_public_class_documentation(rel, declaration))
            elif (
                isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef))
                and is_public
                and not _is_overload_declaration(declaration)
                and not _has_meaningful_python_docstring(declaration)
            ):
                diagnostics.append(f"{rel}:missing-public-function-docstring:{declaration.name}")

    return sorted(diagnostics)


def _is_meaningful_documentation(value: str) -> bool:
    """Return whether *value* contains a non-placeholder purpose statement."""
    cleaned_lines = [
        line.strip().lstrip("#/*<!- ").rstrip("#*/>- ").strip() for line in value.splitlines()
    ]
    cleaned = " ".join(part for part in cleaned_lines if part).strip()
    words = [
        word.lower() for word in cleaned.replace("_", " ").split() if any(c.isalpha() for c in word)
    ]
    normalized_words = {
        "".join(character for character in word if character.isalnum()) for word in words
    }
    return (
        len(cleaned) >= 12
        and len(words) >= 2
        and bool(normalized_words - _PLACEHOLDER_DOCUMENTATION_WORDS)
        and normalized_words.isdisjoint(_PLACEHOLDER_MARKER_WORDS)
    )


def _is_license_comment(value: str) -> bool:
    """Return whether *value* is a leading license notice rather than module docs."""
    lowered = value.lower().strip("#/*<!-> \r\n\t")
    return lowered.startswith("license:") or any(marker in lowered for marker in _LICENSE_MARKERS)


def _read_source(path: Path) -> str | None:
    """Read *path* as UTF-8-compatible source, returning None on an I/O failure."""
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def _without_shebang(text: str) -> str:
    """Remove one leading shebang while preserving all other source text."""
    stripped = text.lstrip("\r\n")
    if stripped.startswith("#!"):
        _, separator, remainder = stripped.partition("\n")
        return remainder if separator else ""
    return stripped


def _extract_block_comment(text: str, opener: str, closer: str) -> tuple[str, str] | None:
    """Return the leading block comment body and remaining text, when present."""
    stripped = text.lstrip()
    if not stripped.startswith(opener):
        return None
    end = stripped.find(closer, len(opener))
    if end == -1:
        return None
    return stripped[len(opener) : end], stripped[end + len(closer) :]


def _skip_leading_license_blocks(text: str, opener: str, closer: str) -> str:
    """Skip reviewed leading license blocks so the following purpose block is checked."""
    remainder = text
    while True:
        block = _extract_block_comment(remainder, opener, closer)
        if block is None or not _is_license_comment(block[0]):
            return remainder
        remainder = block[1]


def _has_jsdoc_header(text: str) -> bool:
    """Return whether JavaScript-family *text* begins with meaningful JSDoc."""
    remainder = _without_shebang(text)
    remainder = _skip_leading_license_blocks(remainder, "/*", "*/")
    block = _extract_block_comment(remainder, "/**", "*/")
    return block is not None and _is_meaningful_documentation(block[0])


def _swift_file_doc_block(lines: list[str]) -> tuple[int, int, str] | None:
    """Return the leading Swift file-purpose DocC block and its line bounds."""
    cursor = 0
    while cursor < len(lines) and (
        not lines[cursor].strip()
        or (lines[cursor].lstrip().startswith("//") and _is_license_comment(lines[cursor]))
    ):
        cursor += 1
    start = cursor
    documentation: list[str] = []
    while cursor < len(lines):
        stripped = lines[cursor].lstrip()
        if not stripped.startswith("///"):
            break
        documentation.append(stripped[3:])
        cursor += 1
    if not documentation:
        return None
    return start, cursor, "\n".join(documentation)


def _has_swift_doc_header(text: str) -> bool:
    """Return whether Swift *text* begins with a meaningful DocC line block."""
    block = _swift_file_doc_block(_without_shebang(text).splitlines())
    return block is not None and _is_meaningful_documentation(block[2])


def _swift_doc_block_before(lines: list[str], line_index: int) -> tuple[int, int, str] | None:
    """Return the DocC block immediately attached to a Swift declaration."""
    cursor = line_index - 1
    while cursor >= 0 and _SWIFT_ATTRIBUTE_LINE.fullmatch(lines[cursor]):
        cursor -= 1
    end = cursor + 1
    documentation: list[str] = []
    while cursor >= 0:
        stripped = lines[cursor].lstrip()
        if not stripped.startswith("///"):
            break
        documentation.append(stripped[3:])
        cursor -= 1
    if not documentation:
        return None
    documentation.reverse()
    return cursor + 1, end, "\n".join(documentation)


def _check_swift_documentation(rel: str, path: Path) -> list[str]:
    """Return file-purpose and callable DocC diagnostics for one Swift source file."""
    source = _read_source(path)
    if source is None:
        return [f"{rel}:missing-file-header-comment"]

    lines = _without_shebang(source).splitlines()
    file_block = _swift_file_doc_block(lines)
    diagnostics: list[str] = []
    if file_block is None or not _is_meaningful_documentation(file_block[2]):
        diagnostics.append(f"{rel}:missing-file-header-comment")

    for line_index, line in enumerate(lines):
        match = _SWIFT_CALLABLE_DECLARATION.match(line)
        if match is None:
            continue
        callable_name = (
            match.group("function") or match.group("initializer") or match.group("subscript")
        )
        declaration_block = _swift_doc_block_before(lines, line_index)
        if declaration_block is None or (
            file_block is not None and declaration_block[:2] == file_block[:2]
        ):
            diagnostics.append(f"{rel}:missing-swift-callable-documentation:{callable_name}")
        elif not _is_meaningful_documentation(declaration_block[2]):
            diagnostics.append(f"{rel}:placeholder-swift-callable-documentation:{callable_name}")

    return sorted(diagnostics)


def _has_hash_header(text: str) -> bool:
    """Return whether shell/config *text* begins with a meaningful hash-comment block."""
    lines = _without_shebang(text).splitlines()
    while True:
        while lines and not lines[0].strip():
            lines.pop(0)
        block_length = 0
        while block_length < len(lines) and lines[block_length].lstrip().startswith("#"):
            block_length += 1
        if block_length == 0 or not _is_license_comment("\n".join(lines[:block_length])):
            break
        del lines[:block_length]
    documentation: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            break
        documentation.append(stripped[1:])
    return _is_meaningful_documentation("\n".join(documentation))


def _has_markup_header(text: str) -> bool:
    """Return whether HTML *text* begins with a meaningful comment block."""
    remainder = _skip_leading_license_blocks(text, "<!--", "-->")
    block = _extract_block_comment(remainder, "<!--", "-->")
    return block is not None and _is_meaningful_documentation(block[0])


def _has_css_header(text: str) -> bool:
    """Return whether CSS *text* begins with a meaningful comment block."""
    remainder = _skip_leading_license_blocks(text, "/*", "*/")
    block = _extract_block_comment(remainder, "/*", "*/")
    return block is not None and _is_meaningful_documentation(block[0])


def _has_file_level_comment(path: Path) -> bool:
    """Return whether *path* starts with meaningful, language-appropriate docs."""
    text = _read_source(path)
    if text is None:
        return False

    suffix = path.suffix.lower()
    if suffix in {".ts", ".tsx", ".js", ".mjs"}:
        return _has_jsdoc_header(text)
    if suffix == ".swift":
        return _has_swift_doc_header(text)
    if suffix == ".html":
        return _has_markup_header(text)
    if suffix == ".css":
        return _has_css_header(text)
    return _has_hash_header(text)


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
        return _check_python_documentation(
            rel,
            path,
            check_public_declarations=classification in {"first-party-code", "first-party-script"},
        )
    if suffix == ".swift":
        return _check_swift_documentation(rel, path)
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
        if rel == PERMANENT_BASELINE_PATH:
            diagnostics.append(f"{rel}:permanent-baseline-forbidden")
            continue
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
    """Return Git-tracked relative paths that remain in the working tree."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 — git is a well-known system executable
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        relative_path
        for relative_path in result.stdout.split("\0")
        if relative_path
        and ((root / relative_path).exists() or (root / relative_path).is_symlink())
    ]


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
    args = parser.parse_args(argv)
    root: Path = args.root

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
