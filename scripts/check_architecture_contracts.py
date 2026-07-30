#!/usr/bin/env python3
"""Enforce the repository's public façades and dependency direction."""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable, Sequence

PUBLIC_FACADE_MODULES: Final[tuple[str, ...]] = (
    "ancestryllm.application",
    "ancestryllm.application.dto",
    "ancestryllm.application.errors",
    "ancestryllm.application.operations",
    "ancestryllm.application.ports",
    "ancestryllm.cli",
    "ancestryllm.core.commands",
    "ancestryllm.core.errors",
    "ancestryllm.core.modules",
    "ancestryllm.domain",
    "ancestryllm.domain.errors",
    "ancestryllm.domain.models",
    "ancestryllm.gedcom",
    "ancestryllm.gedcom.contracts",
    "ancestryllm.gedcom.graph",
    "ancestryllm.gedcom.identity",
    "ancestryllm.gedcom.parser",
    "ancestryllm.gedcom.quality",
    "ancestryllm.gedcom.serialization",
    "ancestryllm.gedcom.service",
    "ancestryllm.gedcom.sync",
    "ancestryllm.llm.contracts",
    "ancestryllm.llm.service",
    "ancestryllm.rootsmagic.service",
)

PRIVATE_MODULE_OWNERS: Final[dict[str, str]] = {
    "ancestryllm.gedcom.engine": "ancestryllm.gedcom",
    "ancestryllm.gedcom.incremental": "ancestryllm.gedcom",
    "ancestryllm.rootsmagic.exporter": "ancestryllm.rootsmagic",
    "ancestryllm.rootsmagic.reader": "ancestryllm.rootsmagic",
    "ancestryllm.rootsmagic.schema_adapter": "ancestryllm.rootsmagic",
}

ADAPTER_OWNERS: Final[dict[str, str]] = {
    "ancestryllm.cli": "terminal-cli",
    "ancestryllm.console": "terminal-repl",
    "ancestryllm.api": "future-fastapi",
    "ancestryllm.desktop": "future-electron",
    "ancestryllm.electron": "future-electron",
}

PURE_CORE_MODULES: Final[frozenset[str]] = frozenset(
    {
        "ancestryllm.core.cancellation",
        "ancestryllm.core.commands",
        "ancestryllm.core.errors",
    }
)

HOST_OBJECT_MODULES: Final[frozenset[str]] = frozenset({"os", "pathlib"})


@dataclass(frozen=True, slots=True)
class DependencyException:
    """One exact, temporary dependency inversion with accountable removal."""

    importer: str
    imported: str
    names: tuple[str, ...]
    owner: str
    issue: str
    reason: str


TEMPORARY_EXCEPTIONS: Final[tuple[DependencyException, ...]] = (
    DependencyException(
        importer="ancestryllm.cli",
        imported="ancestryllm.console.presentation",
        names=("PresentationAdapter",),
        owner="CLI/REPL executor migration",
        issue="#42",
        reason="The current CLI still delegates terminal rendering to the REPL adapter.",
    ),
    DependencyException(
        importer="ancestryllm.cli",
        imported="ancestryllm.console.shell",
        names=("run_repl",),
        owner="CLI/REPL executor migration",
        issue="#42",
        reason="The one-shot CLI remains the compatibility entry point for launching the REPL.",
    ),
    DependencyException(
        importer="ancestryllm.console.parser",
        imported="ancestryllm.cli",
        names=("build_parser",),
        owner="CLI/REPL executor migration",
        issue="#42",
        reason="The REPL deliberately reuses the shipped CLI parser until both use CommandExecutor.",
    ),
    DependencyException(
        importer="ancestryllm.console.shell",
        imported="ancestryllm.cli",
        names=("dispatch",),
        owner="CLI/REPL executor migration",
        issue="#42",
        reason="The REPL deliberately reuses shipped CLI dispatch until both use CommandExecutor.",
    ),
)


@dataclass(frozen=True, slots=True)
class ImportReference:
    path: Path
    line: int
    importer: str
    imported: str
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    code: str
    message: str

    def format(self, root: Path) -> str:
        try:
            display_path = self.path.relative_to(root.parent)
        except ValueError:
            display_path = self.path
        return f"{display_path}:{self.line}: {self.code} {self.message}"


@dataclass(frozen=True, slots=True)
class ArchitectureReport:
    violations: tuple[Violation, ...]
    used_exceptions: frozenset[DependencyException]

    @property
    def passed(self) -> bool:
        return not self.violations


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_from_module(importer: str, is_package: bool, module: str | None, level: int) -> str:
    if level == 0:
        return module or ""
    package_parts = importer.split(".") if is_package else importer.split(".")[:-1]
    keep = len(package_parts) - (level - 1)
    base = package_parts[: max(keep, 0)]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _imports(root: Path, path: Path, module: str) -> tuple[ImportReference, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    is_package = path.name == "__init__.py"
    references: list[ImportReference] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                references.append(
                    ImportReference(
                        path=path,
                        line=node.lineno,
                        importer=module,
                        imported=alias.name,
                        names=(),
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            imported = _resolve_from_module(module, is_package, node.module, node.level)
            references.append(
                ImportReference(
                    path=path,
                    line=node.lineno,
                    importer=module,
                    imported=imported,
                    names=tuple(sorted(alias.name for alias in node.names)),
                )
            )
    return tuple(references)


def _literal_all(tree: ast.Module) -> tuple[str, ...] | None:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        value = node.value
        if not isinstance(value, (ast.List, ast.Tuple)):
            return None
        exports: list[str] = []
        for element in value.elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                return None
            exports.append(element.value)
        return tuple(exports)
    return None


def _bound_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Import):
            names.update(
                alias.asname or alias.name.split(".", maxsplit=1)[0] for alias in node.names
            )
    return names


def _facade_exports(
    root: Path,
    module_paths: dict[str, Path],
    public_facades: Sequence[str],
) -> tuple[dict[str, frozenset[str]], list[Violation]]:
    exports: dict[str, frozenset[str]] = {}
    violations: list[Violation] = []
    for module in public_facades:
        path = module_paths.get(module)
        if path is None:
            violations.append(
                Violation(
                    path=root,
                    line=1,
                    code="ARCH001",
                    message=f"declared public façade {module!r} does not exist",
                )
            )
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        declared = _literal_all(tree)
        if declared is None:
            violations.append(
                Violation(
                    path=path,
                    line=1,
                    code="ARCH002",
                    message=f"public façade {module!r} must declare a literal __all__ allowlist",
                )
            )
            continue
        duplicate = next((name for name in declared if declared.count(name) > 1), None)
        if duplicate is not None:
            violations.append(
                Violation(
                    path=path,
                    line=1,
                    code="ARCH003",
                    message=f"public façade {module!r} exports {duplicate!r} more than once",
                )
            )
        missing = sorted(set(declared) - _bound_names(tree))
        if missing:
            violations.append(
                Violation(
                    path=path,
                    line=1,
                    code="ARCH004",
                    message=(
                        f"public façade {module!r} lists unbound symbols: {', '.join(missing)}"
                    ),
                )
            )
        exports[module] = frozenset(declared)
    return exports, violations


def _owner_for(module: str, owners: dict[str, str]) -> str | None:
    candidates = [
        (prefix, owner)
        for prefix, owner in owners.items()
        if module == prefix or module.startswith(f"{prefix}.")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: len(item[0]))[1]


def _targets(reference: ImportReference, module_names: frozenset[str]) -> tuple[str, ...]:
    targets = [reference.imported]
    for name in reference.names:
        child = f"{reference.imported}.{name}"
        if child in module_names:
            targets.append(child)
    return tuple(target for target in targets if target)


def _pure_layer(module: str) -> str | None:
    if module == "ancestryllm.domain" or module.startswith("ancestryllm.domain."):
        return "domain"
    if module in PURE_CORE_MODULES:
        return "core-contract"
    if module == "ancestryllm.application" or (
        module.startswith("ancestryllm.application.")
        and not module.rsplit(".", 1)[-1].startswith("_")
    ):
        return "application-contract"
    return None


def _allowed_pure_internal(layer: str, target: str) -> bool:
    if layer == "domain":
        return target == "ancestryllm.domain" or target.startswith("ancestryllm.domain.")
    if layer == "core-contract":
        return target in PURE_CORE_MODULES
    return (
        target == "ancestryllm.application"
        or (
            target.startswith("ancestryllm.application.")
            and not target.rsplit(".", 1)[-1].startswith("_")
        )
        or target == "ancestryllm.domain"
        or target.startswith("ancestryllm.domain.")
        or target in PURE_CORE_MODULES
    )


def _matches_exception(
    reference: ImportReference,
    exception: DependencyException,
) -> bool:
    return (
        reference.importer == exception.importer
        and reference.imported == exception.imported
        and reference.names == tuple(sorted(exception.names))
    )


def check_tree(
    root: Path,
    *,
    public_facades: Sequence[str] = PUBLIC_FACADE_MODULES,
    exceptions: Sequence[DependencyException] = TEMPORARY_EXCEPTIONS,
    require_all_exceptions: bool = True,
    enforce_facades: bool = True,
) -> ArchitectureReport:
    """Return every architecture violation under an ``ancestryllm`` source root."""

    root = root.resolve()
    paths = tuple(sorted(root.rglob("*.py")))
    module_paths = {_module_name(root, path): path for path in paths}
    module_names = frozenset(module_paths)
    references = tuple(
        reference
        for module, path in module_paths.items()
        for reference in _imports(root, path, module)
    )
    violations: list[Violation] = []
    exports: dict[str, frozenset[str]] = {}
    if enforce_facades:
        exports, facade_violations = _facade_exports(root, module_paths, public_facades)
        violations.extend(facade_violations)

    used_exceptions: set[DependencyException] = set()
    for reference in references:
        matched = next(
            (exception for exception in exceptions if _matches_exception(reference, exception)),
            None,
        )
        if matched is not None:
            used_exceptions.add(matched)
            continue

        layer = _pure_layer(reference.importer)
        root_import = reference.imported.split(".", maxsplit=1)[0]
        if layer is not None:
            if root_import == "ancestryllm":
                if not _allowed_pure_internal(layer, reference.imported):
                    violations.append(
                        Violation(
                            path=reference.path,
                            line=reference.line,
                            code="ARCH101",
                            message=(
                                f"{layer} module {reference.importer!r} imports "
                                f"disallowed internal dependency {reference.imported!r}"
                            ),
                        )
                    )
            elif root_import in HOST_OBJECT_MODULES:
                violations.append(
                    Violation(
                        path=reference.path,
                        line=reference.line,
                        code="ARCH102",
                        message=(
                            f"{layer} module {reference.importer!r} imports host-object "
                            f"module {root_import!r}; pass a transport-neutral value or port instead"
                        ),
                    )
                )
            elif root_import not in sys.stdlib_module_names and root_import != "__future__":
                violations.append(
                    Violation(
                        path=reference.path,
                        line=reference.line,
                        code="ARCH103",
                        message=(
                            f"{layer} module {reference.importer!r} imports third-party "
                            f"dependency {reference.imported!r}"
                        ),
                    )
                )

        for target in _targets(reference, module_names):
            private_owner = next(
                (
                    owner
                    for private_module, owner in PRIVATE_MODULE_OWNERS.items()
                    if target == private_module or target.startswith(f"{private_module}.")
                ),
                None,
            )
            if private_owner is not None and not (
                reference.importer == private_owner
                or reference.importer.startswith(f"{private_owner}.")
            ):
                violations.append(
                    Violation(
                        path=reference.path,
                        line=reference.line,
                        code="ARCH201",
                        message=(
                            f"{reference.importer!r} imports private owner module {target!r}; "
                            f"use the public {private_owner!r} façade"
                        ),
                    )
                )

            target_adapter = _owner_for(target, ADAPTER_OWNERS)
            importer_adapter = _owner_for(reference.importer, ADAPTER_OWNERS)
            if target_adapter is not None and reference.importer != "ancestryllm.__main__":
                if importer_adapter != target_adapter:
                    violations.append(
                        Violation(
                            path=reference.path,
                            line=reference.line,
                            code="ARCH202",
                            message=(
                                f"{reference.importer!r} crosses into adapter "
                                f"{target_adapter!r} via {target!r}; depend on the "
                                "application façade or add one exact, owned exception"
                            ),
                        )
                    )

        allowed = exports.get(reference.imported)
        if allowed is not None:
            requested_symbols = tuple(
                name
                for name in reference.names
                if f"{reference.imported}.{name}" not in module_names and name != "*"
            )
            undeclared = sorted(set(requested_symbols) - allowed)
            if undeclared:
                violations.append(
                    Violation(
                        path=reference.path,
                        line=reference.line,
                        code="ARCH301",
                        message=(
                            f"{reference.importer!r} imports undeclared symbols from public "
                            f"façade {reference.imported!r}: {', '.join(undeclared)}"
                        ),
                    )
                )

    if require_all_exceptions:
        for exception in set(exceptions) - used_exceptions:
            violations.append(
                Violation(
                    path=root,
                    line=1,
                    code="ARCH401",
                    message=(
                        f"temporary exception is stale or expanded: {exception.importer!r} -> "
                        f"{exception.imported!r} {exception.names!r} "
                        f"(owner: {exception.owner}; removal: {exception.issue})"
                    ),
                )
            )

    return ArchitectureReport(
        violations=tuple(
            sorted(
                violations,
                key=lambda item: (str(item.path), item.line, item.code, item.message),
            )
        ),
        used_exceptions=frozenset(used_exceptions),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src" / "ancestryllm",
        help="ancestryllm package root (default: repository src/ancestryllm)",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = check_tree(args.root)
    if report.violations:
        for violation in report.violations:
            print(violation.format(args.root), file=sys.stderr)
        print(
            f"architecture contract check failed: {len(report.violations)} violation(s)",
            file=sys.stderr,
        )
        return 1
    print(
        "architecture contract check passed "
        f"({len(report.used_exceptions)} temporary exception(s), all exact and live)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
