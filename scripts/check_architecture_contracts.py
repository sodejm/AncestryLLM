#!/usr/bin/env python3
"""Enforce the repository's public façades and dependency direction."""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

PUBLIC_FACADE_MODULES: Final[tuple[str, ...]] = (
    "ancestryllm.application",
    "ancestryllm.application.dto",
    "ancestryllm.application.errors",
    "ancestryllm.application.executor",
    "ancestryllm.application.genealogy",
    "ancestryllm.application.operations",
    "ancestryllm.application.ports",
    "ancestryllm.cli",
    "ancestryllm.core.commands",
    "ancestryllm.core.deployment",
    "ancestryllm.core.errors",
    "ancestryllm.core.modules",
    "ancestryllm.domain",
    "ancestryllm.domain.errors",
    "ancestryllm.domain.genealogy",
    "ancestryllm.domain.models",
    "ancestryllm.gedcom",
    "ancestryllm.gedcom.contracts",
    "ancestryllm.gedcom.graph",
    "ancestryllm.gedcom.identity",
    "ancestryllm.gedcom.model",
    "ancestryllm.gedcom.parser",
    "ancestryllm.gedcom.quality",
    "ancestryllm.gedcom.serializer",
    "ancestryllm.gedcom.serialization",
    "ancestryllm.gedcom.service",
    "ancestryllm.gedcom.sync",
    "ancestryllm.gedcom.sync_kernel",
    "ancestryllm.gedcom.validator",
    "ancestryllm.llm.contracts",
    "ancestryllm.llm.service",
    "ancestryllm.rootsmagic",
    "ancestryllm.rootsmagic.core",
    "ancestryllm.rootsmagic.export",
    "ancestryllm.rootsmagic.query",
    "ancestryllm.rootsmagic.service",
)

PRIVATE_MODULE_OWNERS: Final[dict[str, str]] = {
    "ancestryllm.gedcom.engine": "ancestryllm.gedcom",
    "ancestryllm.gedcom.incremental": "ancestryllm.gedcom",
    "ancestryllm.rootsmagic.exporter": "ancestryllm.rootsmagic",
    "ancestryllm.rootsmagic.reader": "ancestryllm.rootsmagic",
    "ancestryllm.rootsmagic.schema": "ancestryllm.rootsmagic",
    "ancestryllm.rootsmagic.schema_adapter": "ancestryllm.rootsmagic",
    "ancestryllm.rootsmagic.source": "ancestryllm.rootsmagic",
}

# Only these narrow gateways may adapt compatibility implementations to the
# supported façades above. Feature services are deliberately absent: they must
# consume public contracts even when they share the same package owner.
PRIVATE_MODULE_GATEWAYS: Final[dict[str, frozenset[str]]] = {
    "ancestryllm.gedcom.engine": frozenset(),
    "ancestryllm.gedcom.incremental": frozenset(),
    "ancestryllm.rootsmagic.exporter": frozenset(
        {"ancestryllm.rootsmagic", "ancestryllm.rootsmagic.export"}
    ),
    "ancestryllm.rootsmagic.reader": frozenset(
        {
            "ancestryllm.rootsmagic.core",
            "ancestryllm.rootsmagic.exporter",
            "ancestryllm.rootsmagic.schema_adapter",
        }
    ),
    "ancestryllm.rootsmagic.schema": frozenset(
        {
            "ancestryllm.rootsmagic.core",
            "ancestryllm.rootsmagic.schema_adapter",
        }
    ),
    "ancestryllm.rootsmagic.schema_adapter": frozenset(
        {
            "ancestryllm.rootsmagic.core",
            "ancestryllm.rootsmagic.exporter",
        }
    ),
    "ancestryllm.rootsmagic.source": frozenset(
        {
            "ancestryllm.rootsmagic.core",
            "ancestryllm.rootsmagic.reader",
            "ancestryllm.rootsmagic.schema",
        }
    ),
}

# Public owner modules keep a small private implementation surface for
# composing physically owned behavior. These exact importers may use undeclared
# symbols without turning those symbols into supported consumer API.
PUBLIC_FACADE_INTERNAL_GATEWAYS: Final[dict[str, frozenset[str]]] = {
    "ancestryllm.gedcom.graph": frozenset(
        {
            "ancestryllm.gedcom.engine",
            "ancestryllm.gedcom.parser",
            "ancestryllm.gedcom.quality",
            "ancestryllm.gedcom.serialization",
            "ancestryllm.gedcom.sync_gedcom",
        }
    ),
    "ancestryllm.gedcom.identity": frozenset(
        {
            "ancestryllm.gedcom.engine",
            "ancestryllm.gedcom.graph",
            "ancestryllm.gedcom.parser",
            "ancestryllm.gedcom.quality",
            "ancestryllm.gedcom.serialization",
            "ancestryllm.gedcom.sync_gedcom",
        }
    ),
    "ancestryllm.gedcom.quality": frozenset(
        {"ancestryllm.gedcom.engine", "ancestryllm.gedcom.serialization"}
    ),
}

ADAPTER_OWNERS: Final[dict[str, str]] = {
    "ancestryllm.cli": "terminal",
    "ancestryllm.console": "terminal",
    "ancestryllm.container_gateway": "future-fastapi",
    "ancestryllm.container_healthcheck": "future-fastapi",
    "ancestryllm.terminal": "terminal",
    "ancestryllm.api": "future-fastapi",
    "ancestryllm.desktop": "future-electron",
    "ancestryllm.electron": "future-electron",
}

PURE_CORE_MODULES: Final[frozenset[str]] = frozenset(
    {
        "ancestryllm.core.cancellation",
        "ancestryllm.core.commands",
        "ancestryllm.core.deployment",
        "ancestryllm.core.errors",
        "ancestryllm.core.jobs",
    }
)

PURE_GEDCOM_DOCUMENT_MODULES: Final[frozenset[str]] = frozenset(
    {
        "ancestryllm.gedcom.model",
        "ancestryllm.gedcom.serializer",
        "ancestryllm.gedcom.validator",
    }
)

PURE_GEDCOM_OPERATION_MODULES: Final[frozenset[str]] = frozenset(
    {
        "ancestryllm.gedcom.graph",
        "ancestryllm.gedcom.identity",
        "ancestryllm.gedcom.quality",
        "ancestryllm.gedcom.sync_algorithms",
        "ancestryllm.gedcom.sync_kernel",
    }
)

GEDCOM_OPERATION_FORBIDDEN_INTERNAL_PREFIXES: Final[tuple[str, ...]] = (
    "ancestryllm.api",
    "ancestryllm.cli",
    "ancestryllm.console",
    "ancestryllm.core.config",
    "ancestryllm.core.publication",
    "ancestryllm.core.secrets",
    "ancestryllm.desktop",
    "ancestryllm.electron",
    "ancestryllm.execution",
    "ancestryllm.llm.providers",
    "ancestryllm.terminal",
)

GEDCOM_OPERATION_FORBIDDEN_EXTERNAL_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "anthropic",
        "click",
        "electron",
        "fastapi",
        "google",
        "httpx",
        "keyring",
        "openai",
        "prompt_toolkit",
        "rich",
    }
)

HOST_OBJECT_MODULES: Final[frozenset[str]] = frozenset({"os", "pathlib"})

STANDARD_MODULE_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {"__doc__", "__loader__", "__name__", "__package__", "__spec__"}
)


@dataclass(frozen=True, slots=True)
class DependencyException:
    """One exact, temporary dependency inversion with accountable removal."""

    importer: str
    imported: str
    names: tuple[str, ...]
    owner: str
    issue: str
    reason: str


TEMPORARY_EXCEPTIONS: Final[tuple[DependencyException, ...]] = ()


@dataclass(frozen=True, slots=True)
class ConsumerImportException:
    """One exact implementation-characterization import with a removal lifecycle."""

    importer: str
    imported: str
    names: tuple[str, ...]
    owner: str
    issue: str
    reason: str
    lifecycle: str


# One test deliberately verifies each retained legacy import path. These are
# compatibility promises, and any import shape change makes the exception stale
# instead of silently expanding the supported surface.
CHARACTERIZATION_IMPORT_EXCEPTIONS: Final[tuple[ConsumerImportException, ...]] = (
    ConsumerImportException(
        importer="tests.modular.test_incremental",
        imported="ancestryllm.gedcom.engine",
        names=(),
        owner="GEDCOM legacy import compatibility",
        issue="#157 / #166",
        reason="Verifies that the thin engine façade reexports its supported owner contract.",
        lifecycle="Remove only in a breaking release after the legacy import is deprecated.",
    ),
    ConsumerImportException(
        importer="tests.modular.test_incremental",
        imported="ancestryllm.gedcom.incremental",
        names=(),
        owner="incremental legacy import compatibility",
        issue="#157 / #166",
        reason="Verifies that the thin incremental façade reexports its supported owner contracts.",
        lifecycle="Remove only in a breaking release after the legacy import is deprecated.",
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
    used_exceptions: frozenset[DependencyException | ConsumerImportException]

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
            references.extend(
                ImportReference(
                    path=path,
                    line=node.lineno,
                    importer=module,
                    imported=alias.name,
                    names=(),
                )
                for alias in node.names
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
    if module in PURE_GEDCOM_DOCUMENT_MODULES:
        return "gedcom-document"
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
    if layer == "gedcom-document":
        return target in PURE_GEDCOM_DOCUMENT_MODULES
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


def _is_forbidden_gedcom_operation_dependency(imported: str) -> bool:
    root = imported.split(".", maxsplit=1)[0]
    return root in GEDCOM_OPERATION_FORBIDDEN_EXTERNAL_ROOTS or any(
        imported == prefix or imported.startswith(f"{prefix}.")
        for prefix in GEDCOM_OPERATION_FORBIDDEN_INTERNAL_PREFIXES
    )


def _matches_exception(
    reference: ImportReference,
    exception: DependencyException | ConsumerImportException,
) -> bool:
    return (
        reference.importer == exception.importer
        and reference.imported == exception.imported
        and reference.names == tuple(sorted(exception.names))
    )


def _repository_module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _private_gedcom_targets(reference: ImportReference) -> tuple[str, ...]:
    private_modules = (
        "ancestryllm.gedcom.engine",
        "ancestryllm.gedcom.incremental",
    )
    targets: list[str] = []
    for private_module in private_modules:
        if reference.imported == private_module or reference.imported.startswith(
            f"{private_module}."
        ):
            targets.append(private_module)
        if any(f"{reference.imported}.{name}" == private_module for name in reference.names):
            targets.append(private_module)
    return tuple(targets)


@dataclass(frozen=True, slots=True)
class _ModuleBinding:
    """One lexically bound module name and the modules imported through it."""

    path: str
    imported_modules: frozenset[str]

    def child(self, name: str) -> _ModuleBinding | None:
        path = f"{self.path}.{name}"
        if any(
            imported == path or imported.startswith(f"{path}.")
            for imported in self.imported_modules
        ):
            return _ModuleBinding(path=path, imported_modules=self.imported_modules)
        return None


@dataclass(slots=True)
class _LexicalScope:
    """Bindings visible at one Python lexical scope while walking in source order."""

    kind: str
    parent: _LexicalScope | None
    local_names: frozenset[str] = frozenset()
    aliases: dict[str, _ModuleBinding] = field(default_factory=dict)
    shadowed: set[str] = field(default_factory=set)


class _LocalBindingCollector(ast.NodeVisitor):
    """Collect names local to one function without descending into child scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(
            alias.asname or alias.name.split(".", maxsplit=1)[0] for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocals.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.names.add(node.name)
        for statement in node.body:
            self.visit(statement)

    def visit_Match(self, node: ast.Match) -> None:
        for case in node.cases:
            self.names.update(_pattern_names(case.pattern))
            if case.guard is not None:
                self.visit(case.guard)
            for statement in case.body:
                self.visit(statement)


def _argument_names(arguments: ast.arguments) -> set[str]:
    names = {
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    }
    if arguments.vararg is not None:
        names.add(arguments.vararg.arg)
    if arguments.kwarg is not None:
        names.add(arguments.kwarg.arg)
    return names


def _function_local_names(
    body: Sequence[ast.stmt],
    arguments: ast.arguments,
) -> frozenset[str]:
    collector = _LocalBindingCollector()
    for statement in body:
        collector.visit(statement)
    names = collector.names | _argument_names(arguments)
    names.difference_update(collector.globals | collector.nonlocals)
    return frozenset(names)


def _target_names(target: ast.AST) -> set[str]:
    return {
        node.id
        for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    }


def _pattern_names(pattern: ast.pattern) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            names.add(node.rest)
    return names


class _FacadeAccessVisitor(ast.NodeVisitor):
    """Resolve façade module aliases without leaking them across lexical scopes."""

    def __init__(
        self,
        *,
        path: Path,
        importer: str,
        facade_exports: Mapping[str, frozenset[str]],
    ) -> None:
        self.path = path
        self.importer = importer
        self.facade_exports = facade_exports
        self.facades = frozenset(facade_exports)
        self.scope = _LexicalScope(kind="module", parent=None)
        self.violations: list[Violation] = []

    @staticmethod
    def _closure_parent(scope: _LexicalScope) -> _LexicalScope | None:
        current: _LexicalScope | None = scope
        while current is not None and current.kind == "class":
            current = current.parent
        return current

    def _resolve_name(
        self,
        name: str,
        scope: _LexicalScope | None = None,
    ) -> _ModuleBinding | None:
        current = scope or self.scope
        if name in current.aliases:
            return current.aliases[name]
        if name in current.shadowed or name in current.local_names:
            return None
        return self._resolve_name(name, current.parent) if current.parent is not None else None

    def _resolve_module(self, node: ast.expr) -> _ModuleBinding | None:
        if isinstance(node, ast.Name):
            return self._resolve_name(node.id)
        if isinstance(node, ast.Attribute):
            parent = self._resolve_module(node.value)
            return parent.child(node.attr) if parent is not None else None
        return None

    def _bind_alias(self, name: str, binding: _ModuleBinding) -> None:
        existing = self.scope.aliases.get(name)
        if existing is not None and existing.path == binding.path:
            binding = _ModuleBinding(
                path=binding.path,
                imported_modules=existing.imported_modules | binding.imported_modules,
            )
        self.scope.aliases[name] = binding
        self.scope.shadowed.add(name)

    def _shadow(self, names: Iterable[str]) -> None:
        for name in names:
            self.scope.aliases.pop(name, None)
            self.scope.shadowed.add(name)

    def _add_undeclared(self, line: int, facade: str, symbol: str, action: str) -> None:
        self.violations.append(
            Violation(
                path=self.path,
                line=line,
                code="ARCH503",
                message=(
                    f"repository consumer {self.importer!r} {action} undeclared symbol "
                    f"{symbol!r} through supported façade {facade!r}"
                ),
            )
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".", maxsplit=1)[0]
            if alias.name in self.facades:
                path = alias.name if alias.asname else alias.name.split(".", maxsplit=1)[0]
                self._bind_alias(
                    name,
                    _ModuleBinding(path=path, imported_modules=frozenset({alias.name})),
                )
            else:
                self._shadow((name,))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        imported = node.module or ""
        allowed = self.facade_exports.get(imported)
        for alias in node.names:
            if alias.name == "*":
                continue
            child = f"{imported}.{alias.name}"
            name = alias.asname or alias.name
            if child in self.facades:
                self._bind_alias(
                    name,
                    _ModuleBinding(path=child, imported_modules=frozenset({child})),
                )
                continue
            self._shadow((name,))
            if allowed is not None and alias.name not in allowed:
                self._add_undeclared(node.lineno, imported, alias.name, "imports")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        module = self._resolve_module(node.value)
        if module is not None and module.path in self.facade_exports:
            child = f"{module.path}.{node.attr}"
            if (
                child not in self.facades
                and node.attr not in STANDARD_MODULE_ATTRIBUTES
                and node.attr not in self.facade_exports[module.path]
            ):
                self._add_undeclared(node.lineno, module.path, node.attr, "accesses")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)
            self._shadow(_target_names(target))

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.target)
        self._shadow(_target_names(node.target))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)
        self._shadow(_target_names(node.target))

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._shadow(_target_names(node.target))

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.visit(target)
            self._shadow(_target_names(target))

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self._shadow(_target_names(node.target))
        for statement in (*node.body, *node.orelse):
            self.visit(statement)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._shadow(_target_names(node.target))
        for statement in (*node.body, *node.orelse):
            self.visit(statement)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._shadow(_target_names(item.optional_vars))
        for statement in node.body:
            self.visit(statement)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._shadow(_target_names(item.optional_vars))
        for statement in node.body:
            self.visit(statement)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name is not None:
            self._shadow((node.name,))
        for statement in node.body:
            self.visit(statement)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        for case in node.cases:
            self._shadow(_pattern_names(case.pattern))
            if case.guard is not None:
                self.visit(case.guard)
            for statement in case.body:
                self.visit(statement)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)
        self._shadow((node.name,))
        previous = self.scope
        self.scope = _LexicalScope(
            kind="function",
            parent=self._closure_parent(previous),
            local_names=_function_local_names(node.body, node.args),
        )
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.scope = previous

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        previous = self.scope
        self.scope = _LexicalScope(
            kind="lambda",
            parent=self._closure_parent(previous),
            local_names=frozenset(_argument_names(node.args)),
        )
        try:
            self.visit(node.body)
        finally:
            self.scope = previous

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        previous = self.scope
        self.scope = _LexicalScope(kind="class", parent=previous)
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.scope = previous
        self._shadow((node.name,))

    def _visit_comprehension(
        self,
        generators: Sequence[ast.comprehension],
        values: Sequence[ast.expr],
    ) -> None:
        if not generators:
            for value in values:
                self.visit(value)
            return
        self.visit(generators[0].iter)
        previous = self.scope
        local_names = frozenset(
            name for generator in generators for name in _target_names(generator.target)
        )
        self.scope = _LexicalScope(
            kind="comprehension",
            parent=self._closure_parent(previous),
            local_names=local_names,
        )
        try:
            for index, generator in enumerate(generators):
                if index:
                    self.visit(generator.iter)
                self._shadow(_target_names(generator.target))
                for condition in generator.ifs:
                    self.visit(condition)
            for value in values:
                self.visit(value)
        finally:
            self.scope = previous

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))


def _repository_facade_exports(repository_root: Path) -> dict[str, frozenset[str]]:
    package_root = repository_root / "src" / "ancestryllm"
    paths = tuple(sorted(package_root.rglob("*.py"))) if package_root.is_dir() else ()
    module_paths = {_module_name(package_root, path): path for path in paths}
    exports: dict[str, frozenset[str]] = {}
    for module in PUBLIC_FACADE_MODULES:
        if not module.startswith("ancestryllm.gedcom."):
            continue
        path = module_paths.get(module)
        if path is None:
            continue
        declared = _literal_all(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        if declared is not None:
            exports[module] = frozenset(declared)
    return exports


def _private_facade_symbol_violations(
    path: Path,
    importer: str,
    facade_exports: Mapping[str, frozenset[str]],
) -> tuple[Violation, ...]:
    """Reject consumer access not declared by supported GEDCOM façades."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = _FacadeAccessVisitor(
        path=path,
        importer=importer,
        facade_exports=facade_exports,
    )
    visitor.visit(tree)
    return tuple(visitor.violations)


def check_repository_consumers(
    repository_root: Path,
    *,
    exceptions: Sequence[ConsumerImportException] = CHARACTERIZATION_IMPORT_EXCEPTIONS,
    require_all_exceptions: bool = True,
    facade_exports: Mapping[str, frozenset[str]] | None = None,
) -> ArchitectureReport:
    """Reject private GEDCOM imports and façade symbols in tests and scripts."""

    repository_root = repository_root.resolve()
    paths = tuple(
        sorted(
            path
            for directory in (repository_root / "tests", repository_root / "scripts")
            if directory.is_dir()
            for path in directory.rglob("*.py")
        )
    )
    references = tuple(
        reference
        for path in paths
        for reference in _imports(
            repository_root,
            path,
            _repository_module_name(repository_root, path),
        )
    )
    violations: list[Violation] = []
    used_exceptions: set[ConsumerImportException] = set()
    resolved_facade_exports = (
        _repository_facade_exports(repository_root) if facade_exports is None else facade_exports
    )
    for path in paths:
        violations.extend(
            _private_facade_symbol_violations(
                path,
                _repository_module_name(repository_root, path),
                resolved_facade_exports,
            )
        )
    for reference in references:
        private_targets = _private_gedcom_targets(reference)
        if not private_targets:
            continue
        matched = next(
            (exception for exception in exceptions if _matches_exception(reference, exception)),
            None,
        )
        if matched is not None:
            used_exceptions.add(matched)
            continue
        violations.extend(
            Violation(
                path=reference.path,
                line=reference.line,
                code="ARCH501",
                message=(
                    f"repository consumer {reference.importer!r} imports private GEDCOM "
                    f"module {target!r}; use a declared façade symbol or add one exact "
                    "implementation-characterization exception with a removal lifecycle"
                ),
            )
            for target in private_targets
        )

    if require_all_exceptions:
        violations.extend(
            Violation(
                path=repository_root,
                line=1,
                code="ARCH502",
                message=(
                    f"characterization import exception is stale or expanded: "
                    f"{exception.importer!r} -> {exception.imported!r} {exception.names!r} "
                    f"(owner: {exception.owner}; removal: {exception.lifecycle})"
                ),
            )
            for exception in set(exceptions) - used_exceptions
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
        if (
            reference.importer in PURE_GEDCOM_OPERATION_MODULES
            and _is_forbidden_gedcom_operation_dependency(reference.imported)
        ):
            violations.append(
                Violation(
                    path=reference.path,
                    line=reference.line,
                    code="ARCH104",
                    message=(
                        f"GEDCOM operation module {reference.importer!r} imports "
                        f"runtime or adapter dependency {reference.imported!r}; "
                        "inject a transport-neutral value or port instead"
                    ),
                )
            )
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
            elif root_import in HOST_OBJECT_MODULES and layer != "gedcom-document":
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
            private_match = next(
                (
                    (private_module, owner)
                    for private_module, owner in PRIVATE_MODULE_OWNERS.items()
                    if target == private_module or target.startswith(f"{private_module}.")
                ),
                None,
            )
            if private_match is not None:
                private_module, private_owner = private_match
                allowed_gateways = PRIVATE_MODULE_GATEWAYS.get(private_module, frozenset())
            else:
                private_module = ""
                private_owner = None
                allowed_gateways = frozenset()
            if private_owner is not None and reference.importer not in allowed_gateways:
                violations.append(
                    Violation(
                        path=reference.path,
                        line=reference.line,
                        code="ARCH201",
                        message=(
                            f"{reference.importer!r} imports private owner module {target!r}; "
                            f"use the public {private_owner!r} façade through a declared gateway"
                        ),
                    )
                )

            target_adapter = _owner_for(target, ADAPTER_OWNERS)
            importer_adapter = _owner_for(reference.importer, ADAPTER_OWNERS)
            if (
                target_adapter is not None
                and reference.importer != "ancestryllm.__main__"
                and importer_adapter != target_adapter
            ):
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
        internal_gateways = PUBLIC_FACADE_INTERNAL_GATEWAYS.get(
            reference.imported,
            frozenset(),
        )
        if allowed is not None and reference.importer not in internal_gateways:
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
        violations.extend(
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
            for exception in set(exceptions) - used_exceptions
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
    source_report = check_tree(args.root)
    repository_root = args.root.resolve().parents[1]
    consumer_report = check_repository_consumers(repository_root)
    violations = source_report.violations + consumer_report.violations
    if violations:
        for violation in violations:
            print(violation.format(args.root), file=sys.stderr)
        print(
            f"architecture contract check failed: {len(violations)} violation(s)",
            file=sys.stderr,
        )
        return 1
    print(
        "architecture contract check passed "
        f"({len(source_report.used_exceptions)} dependency exception(s), "
        f"{len(consumer_report.used_exceptions)} characterization import exception(s); "
        "all exact and live)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
