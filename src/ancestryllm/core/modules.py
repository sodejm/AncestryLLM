"""Explicit built-in module registry with no third-party discovery."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Protocol

from ancestryllm.core.context import AppContext


class ToolModule(Protocol):
    """Minimum contract implemented by each built-in console module."""

    context: AppContext
    descriptor: ModuleDescriptor


@dataclass(frozen=True, slots=True)
class ModuleDescriptor:
    module_id: str
    name: str
    summary: str
    actions: tuple[str, ...]
    implementation: str
    configuration: tuple[str, ...] = ()
    required_services: tuple[str, ...] = ()


BUILTIN_MODULES: dict[str, ModuleDescriptor] = {
    "rootsmagic": ModuleDescriptor(
        "rootsmagic",
        "RootsMagic",
        "Immutable RootsMagic discovery, query, and GEDCOM export.",
        ("list", "query", "export"),
        "ancestryllm.console.rootsmagic:RootsMagicModule",
        ("storage.family_tree_dirs", "limits.max_query_rows", "limits.query_timeout_seconds"),
        ("rootsmagic", "llm"),
    ),
    "gedcom": ModuleDescriptor(
        "gedcom",
        "GEDCOM",
        "Loss-minimizing merge, subtree, quality, update, and rebase.",
        ("merge", "subtree", "quality", "sync"),
        "ancestryllm.console.gedcom:GedcomModule",
        (),
        ("gedcom", "llm"),
    ),
    "ocr": ModuleDescriptor(
        "ocr",
        "OCR",
        "Schema-validated genealogy extraction from OCR text.",
        ("extract",),
        "ancestryllm.console.ocr:OcrModule",
        (),
        ("llm",),
    ),
    "prompts": ModuleDescriptor(
        "prompts",
        "Prompts",
        "Versioned prompt templates and safe rendering.",
        ("list", "save", "show", "render"),
        "ancestryllm.console.prompts:PromptsModule",
        (),
        ("prompts", "database"),
    ),
    "people": ModuleDescriptor(
        "people",
        "People",
        "Curated encrypted research-person workspace.",
        ("list", "add"),
        "ancestryllm.console.people:PeopleModule",
        (),
        ("research", "database"),
    ),
    "providers": ModuleDescriptor(
        "providers",
        "Providers",
        "Explicit LLM provider profiles and consent status.",
        ("list", "create", "consent", "revoke"),
        "ancestryllm.console.providers:ProvidersModule",
        (),
        ("provider_profiles",),
    ),
    "secrets": ModuleDescriptor(
        "secrets",
        "Secrets",
        "No-echo OS-keyring credential management.",
        ("set", "delete", "status"),
        "ancestryllm.console.secrets:SecretsModule",
        (),
        ("secrets",),
    ),
}


class ModuleRegistry:
    def __init__(self, context: AppContext) -> None:
        self.context = context

    def descriptors(self) -> list[ModuleDescriptor]:
        return [
            BUILTIN_MODULES[name]
            for name in sorted(self.context.config.enabled_modules)
            if name in BUILTIN_MODULES
        ]

    def load(self) -> list[Any]:
        loaded: list[Any] = []
        for descriptor in self.descriptors():
            module_name, class_name = descriptor.implementation.split(":", 1)
            implementation = getattr(importlib.import_module(module_name), class_name)
            loaded.append(implementation(self.context, descriptor))
        return loaded

    def enable(self, module_id: str) -> None:
        if module_id not in BUILTIN_MODULES:
            raise KeyError(module_id)
        self.context.config.enabled_modules.add(module_id)
        self.context.config.save()

    def disable(self, module_id: str) -> None:
        self.context.config.enabled_modules.discard(module_id)
        self.context.config.save()
