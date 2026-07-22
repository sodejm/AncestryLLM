"""Built-in module registry and transport-neutral command specifications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ancestryllm.core.context import AppContext


class ArgumentType(str, Enum):
    """Serializable value types understood by command transports."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    PATH = "path"


class ArgumentAction(str, Enum):
    """Transport-neutral argument collection behavior."""

    STORE = "store"
    APPEND = "append"
    STORE_TRUE = "store_true"


class ArgumentCardinality(str, Enum):
    """Supported variable argument cardinalities."""

    OPTIONAL = "optional"
    ONE_OR_MORE = "one_or_more"
    REMAINDER = "remainder"


class CompletionKind(str, Enum):
    """Semantic completion sources for a future interactive transport."""

    NONE = "none"
    CHOICES = "choices"
    FILE = "file"
    MODULE = "module"
    TREE = "tree"
    PERSON = "person"
    PROMPT = "prompt"
    WORKSPACE = "workspace"
    PROVIDER = "provider"
    MODEL = "model"
    PROFILE = "profile"
    CONSENT = "consent"
    KEYRING_REFERENCE = "keyring_reference"


ArgumentDefault = str | int | float | bool | tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class ArgumentSpec:
    """One positional or flagged command argument, independent of any UI toolkit."""

    name: str
    help: str
    flags: tuple[str, ...] = ()
    value_type: ArgumentType = ArgumentType.STRING
    default: ArgumentDefault = None
    choices: tuple[str, ...] = ()
    required: bool = False
    cardinality: ArgumentCardinality | None = None
    action: ArgumentAction = ArgumentAction.STORE
    metavar: str | None = None
    sensitive: bool = False
    completion: CompletionKind = CompletionKind.NONE

    @property
    def positional(self) -> bool:
        return not self.flags


@dataclass(frozen=True, slots=True)
class ExclusiveArgumentGroup:
    """Arguments that cannot be supplied together."""

    arguments: tuple[str, ...]
    required: bool = False


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """A named action and its complete invocation contract."""

    name: str
    help: str
    arguments: tuple[ArgumentSpec, ...] = ()
    exclusive_groups: tuple[ExclusiveArgumentGroup, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """A command shared by one-shot and future interactive transports."""

    name: str
    help: str
    actions: tuple[ActionSpec, ...]


class ToolModule(Protocol):
    """Minimum contract implemented by each built-in console module."""

    context: AppContext
    descriptor: ModuleDescriptor


@dataclass(frozen=True, slots=True)
class ModuleDescriptor:
    module_id: str
    name: str
    summary: str
    implementation: str
    configuration: tuple[str, ...] = ()
    required_services: tuple[str, ...] = ()

    @property
    def command(self) -> CommandSpec:
        """Return metadata without importing the module implementation or a UI package."""

        return COMMAND_SPECIFICATIONS[self.module_id]

    @property
    def actions(self) -> tuple[str, ...]:
        """Retain the existing descriptor API while deriving names from command metadata."""

        return tuple(action.name for action in self.command.actions)


BUILTIN_MODULES: dict[str, ModuleDescriptor] = {
    "rootsmagic": ModuleDescriptor(
        "rootsmagic",
        "RootsMagic",
        "Immutable RootsMagic discovery, query, and GEDCOM export.",
        "ancestryllm.cli:run_tokens",
        ("storage.family_tree_dirs", "limits.max_query_rows", "limits.query_timeout_seconds"),
        ("rootsmagic", "llm"),
    ),
    "gedcom": ModuleDescriptor(
        "gedcom",
        "GEDCOM",
        "Loss-minimizing merge, subtree, quality, update, and rebase.",
        "ancestryllm.cli:run_tokens",
        (),
        ("gedcom", "llm"),
    ),
    "ocr": ModuleDescriptor(
        "ocr",
        "OCR",
        "Schema-validated genealogy extraction from OCR text.",
        "ancestryllm.cli:run_tokens",
        (),
        ("llm",),
    ),
    "prompts": ModuleDescriptor(
        "prompts",
        "Prompts",
        "Versioned prompt templates and safe rendering.",
        "ancestryllm.cli:run_tokens",
        (),
        ("prompts", "database"),
    ),
    "people": ModuleDescriptor(
        "people",
        "People",
        "Curated encrypted research-person workspace.",
        "ancestryllm.cli:run_tokens",
        (),
        ("research", "database"),
    ),
    "providers": ModuleDescriptor(
        "providers",
        "Providers",
        "Explicit LLM provider profiles and consent status.",
        "ancestryllm.cli:run_tokens",
        (),
        ("provider_profiles",),
    ),
    "secrets": ModuleDescriptor(
        "secrets",
        "Secrets",
        "No-echo OS-keyring credential management.",
        "ancestryllm.cli:run_tokens",
        (),
        ("secrets",),
    ),
}


GLOBAL_ARGUMENTS: tuple[ArgumentSpec, ...] = (
    ArgumentSpec(
        "config",
        "Explicit non-secret config.toml path",
        ("--config",),
        ArgumentType.PATH,
        completion=CompletionKind.FILE,
    ),
    ArgumentSpec(
        "json",
        "Emit machine-readable JSON",
        ("--json",),
        default=False,
        action=ArgumentAction.STORE_TRUE,
    ),
)


_GEDCOM_VERSIONS = ("5.5.5", "5.5.1")
_SCOPES = ("connected", "ancestors", "descendants")
_PROVIDERS = ("ollama", "openai", "anthropic", "gemini", "openrouter")


COMMAND_SPECIFICATIONS: dict[str, CommandSpec] = {
    "modules": CommandSpec(
        "modules",
        "List or configure built-in modules",
        (
            ActionSpec("list", "List enabled built-in modules"),
            ActionSpec(
                "enable",
                "Enable a built-in module",
                (
                    ArgumentSpec(
                        "module_id",
                        "Built-in module identifier",
                        choices=tuple(sorted(BUILTIN_MODULES)),
                        completion=CompletionKind.MODULE,
                    ),
                ),
            ),
            ActionSpec(
                "disable",
                "Disable a built-in module",
                (
                    ArgumentSpec(
                        "module_id",
                        "Built-in module identifier",
                        choices=tuple(sorted(BUILTIN_MODULES)),
                        completion=CompletionKind.MODULE,
                    ),
                ),
            ),
        ),
    ),
    "rootsmagic": CommandSpec(
        "rootsmagic",
        "Immutable RootsMagic operations",
        (
            ActionSpec("list", "List configured RootsMagic trees"),
            ActionSpec(
                "query",
                "Run a read-only SQL or natural-language query",
                (
                    ArgumentSpec(
                        "tree",
                        "Configured tree name",
                        ("--tree",),
                        required=True,
                        sensitive=True,
                        completion=CompletionKind.TREE,
                    ),
                    ArgumentSpec("sql", "Read-only SQL query", ("--sql",), sensitive=True),
                    ArgumentSpec(
                        "question", "Natural-language question", ("--question",), sensitive=True
                    ),
                    ArgumentSpec(
                        "provider",
                        "Provider profile identifier",
                        ("--provider",),
                        default="none",
                        completion=CompletionKind.PROVIDER,
                    ),
                    ArgumentSpec(
                        "model",
                        "Provider model identifier",
                        ("--model",),
                        default="",
                        completion=CompletionKind.MODEL,
                    ),
                    ArgumentSpec(
                        "consent",
                        "Consent grant name",
                        ("--consent",),
                        completion=CompletionKind.CONSENT,
                    ),
                ),
                (ExclusiveArgumentGroup(("sql", "question"), required=True),),
            ),
            ActionSpec(
                "export",
                "Export an immutable RootsMagic tree to GEDCOM",
                (
                    ArgumentSpec(
                        "tree",
                        "Configured tree name",
                        ("--tree",),
                        required=True,
                        sensitive=True,
                        completion=CompletionKind.TREE,
                    ),
                    ArgumentSpec(
                        "output",
                        "Destination GEDCOM path",
                        ("--output",),
                        ArgumentType.PATH,
                        required=True,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                    ArgumentSpec(
                        "profile",
                        "Export preservation profile",
                        ("--profile",),
                        default="portable",
                        choices=("portable", "preservation"),
                        completion=CompletionKind.CHOICES,
                    ),
                    ArgumentSpec(
                        "gedcom_version",
                        "GEDCOM output version",
                        ("--gedcom-version",),
                        default="5.5.5",
                        choices=_GEDCOM_VERSIONS,
                        completion=CompletionKind.CHOICES,
                    ),
                    ArgumentSpec(
                        "destination",
                        "Destination genealogy service",
                        ("--destination",),
                        default="generic",
                        choices=("generic", "ancestry", "geni", "myheritage"),
                        completion=CompletionKind.CHOICES,
                    ),
                    ArgumentSpec(
                        "root_person_id",
                        "Root person identifier",
                        ("--root-person-id",),
                        sensitive=True,
                        completion=CompletionKind.PERSON,
                    ),
                    ArgumentSpec(
                        "scope",
                        "Relationship scope from the root person",
                        ("--scope",),
                        default="connected",
                        choices=_SCOPES,
                        completion=CompletionKind.CHOICES,
                    ),
                    ArgumentSpec(
                        "generations",
                        "Maximum generations from the root person",
                        ("--generations",),
                        ArgumentType.INTEGER,
                    ),
                    ArgumentSpec(
                        "living",
                        "Treatment of living people",
                        ("--living",),
                        default="exclude",
                        choices=("exclude", "redact", "include"),
                        completion=CompletionKind.CHOICES,
                    ),
                    ArgumentSpec(
                        "report",
                        "Optional export report path",
                        ("--report",),
                        ArgumentType.PATH,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                ),
            ),
        ),
    ),
    "gedcom": CommandSpec(
        "gedcom",
        "GEDCOM operations",
        (
            ActionSpec(
                "merge",
                "Merge GEDCOM files with loss-minimizing defaults",
                (
                    ArgumentSpec(
                        "inputs",
                        "Input GEDCOM paths",
                        value_type=ArgumentType.PATH,
                        cardinality=ArgumentCardinality.ONE_OR_MORE,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                    ArgumentSpec(
                        "output",
                        "Destination GEDCOM path",
                        ("--output", "-o"),
                        ArgumentType.PATH,
                        required=True,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                    ArgumentSpec(
                        "root_person",
                        "Root person identifier or name",
                        ("--root-person",),
                        sensitive=True,
                        completion=CompletionKind.PERSON,
                    ),
                    ArgumentSpec(
                        "quality_report",
                        "Optional quality report path",
                        ("--quality-report",),
                        ArgumentType.PATH,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                    ArgumentSpec(
                        "gedcom_version",
                        "GEDCOM output version",
                        ("--gedcom-version",),
                        default="5.5.5",
                        choices=_GEDCOM_VERSIONS,
                        completion=CompletionKind.CHOICES,
                    ),
                    ArgumentSpec(
                        "provider",
                        "Provider profile identifier",
                        ("--provider",),
                        default="none",
                        completion=CompletionKind.PROVIDER,
                    ),
                    ArgumentSpec(
                        "model",
                        "Provider model identifier",
                        ("--model",),
                        default="",
                        completion=CompletionKind.MODEL,
                    ),
                    ArgumentSpec(
                        "consent",
                        "Consent grant name",
                        ("--consent",),
                        completion=CompletionKind.CONSENT,
                    ),
                    ArgumentSpec(
                        "similarity_threshold",
                        "Potential-match similarity threshold",
                        ("--similarity-threshold",),
                        ArgumentType.INTEGER,
                        default=78,
                    ),
                ),
            ),
            ActionSpec(
                "subtree",
                "Export a rooted GEDCOM subtree",
                (
                    ArgumentSpec(
                        "input",
                        "Input GEDCOM path",
                        value_type=ArgumentType.PATH,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                    ArgumentSpec(
                        "output",
                        "Destination GEDCOM path",
                        ("--output", "-o"),
                        ArgumentType.PATH,
                        required=True,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                    ArgumentSpec(
                        "root_person",
                        "Root person identifier or name",
                        ("--root-person",),
                        required=True,
                        sensitive=True,
                        completion=CompletionKind.PERSON,
                    ),
                    ArgumentSpec(
                        "scope",
                        "Relationship scope from the root person",
                        ("--scope",),
                        default="connected",
                        choices=_SCOPES,
                        completion=CompletionKind.CHOICES,
                    ),
                    ArgumentSpec(
                        "generations",
                        "Maximum generations from the root person",
                        ("--generations",),
                        ArgumentType.INTEGER,
                    ),
                    ArgumentSpec(
                        "gedcom_version",
                        "GEDCOM output version",
                        ("--gedcom-version",),
                        default="5.5.5",
                        choices=_GEDCOM_VERSIONS,
                        completion=CompletionKind.CHOICES,
                    ),
                ),
            ),
            ActionSpec(
                "quality",
                "Generate a rooted GEDCOM quality report",
                (
                    ArgumentSpec(
                        "input",
                        "Input GEDCOM path",
                        value_type=ArgumentType.PATH,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                    ArgumentSpec(
                        "output",
                        "Destination report path",
                        ("--output", "-o"),
                        ArgumentType.PATH,
                        required=True,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                    ArgumentSpec(
                        "root_person",
                        "Root person identifier or name",
                        ("--root-person",),
                        required=True,
                        sensitive=True,
                        completion=CompletionKind.PERSON,
                    ),
                ),
            ),
            ActionSpec(
                "sync",
                "Run the incremental update or rebase workflow",
                (
                    ArgumentSpec(
                        "sync_command",
                        "Incremental synchronization operation",
                        choices=("update", "rebase"),
                        completion=CompletionKind.CHOICES,
                    ),
                    ArgumentSpec(
                        "sync_args",
                        "Arguments forwarded to the synchronization operation",
                        cardinality=ArgumentCardinality.REMAINDER,
                        sensitive=True,
                    ),
                ),
            ),
        ),
    ),
    "prompts": CommandSpec(
        "prompts",
        "Versioned saved prompts",
        (
            ActionSpec("list", "List saved prompts"),
            ActionSpec(
                "save",
                "Save a new prompt version",
                (
                    ArgumentSpec(
                        "name", "Prompt name", sensitive=True, completion=CompletionKind.PROMPT
                    ),
                    ArgumentSpec(
                        "purpose", "Prompt purpose", ("--purpose",), required=True, sensitive=True
                    ),
                    ArgumentSpec("body", "Prompt body", ("--body",), sensitive=True),
                    ArgumentSpec(
                        "body_file",
                        "File containing the prompt body",
                        ("--body-file",),
                        ArgumentType.PATH,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                    ArgumentSpec(
                        "variable",
                        "Declared prompt variable",
                        ("--variable",),
                        default=(),
                        action=ArgumentAction.APPEND,
                        sensitive=True,
                    ),
                    ArgumentSpec(
                        "schema_file",
                        "JSON schema file",
                        ("--schema-file",),
                        ArgumentType.PATH,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                    ArgumentSpec(
                        "tag",
                        "Prompt tag",
                        ("--tag",),
                        default=(),
                        action=ArgumentAction.APPEND,
                        sensitive=True,
                    ),
                ),
                (ExclusiveArgumentGroup(("body", "body_file"), required=True),),
            ),
            ActionSpec(
                "show",
                "Show a saved prompt version",
                (
                    ArgumentSpec(
                        "name", "Prompt name", sensitive=True, completion=CompletionKind.PROMPT
                    ),
                    ArgumentSpec("version", "Prompt version", ("--version",), ArgumentType.INTEGER),
                ),
            ),
            ActionSpec(
                "render",
                "Render a saved prompt version",
                (
                    ArgumentSpec(
                        "name", "Prompt name", sensitive=True, completion=CompletionKind.PROMPT
                    ),
                    ArgumentSpec("version", "Prompt version", ("--version",), ArgumentType.INTEGER),
                    ArgumentSpec(
                        "value",
                        "Template value as NAME=VALUE",
                        ("--value",),
                        default=(),
                        action=ArgumentAction.APPEND,
                        metavar="NAME=VALUE",
                        sensitive=True,
                    ),
                ),
            ),
        ),
    ),
    "people": CommandSpec(
        "people",
        "Encrypted research workspace",
        (
            ActionSpec(
                "list",
                "List research people",
                (
                    ArgumentSpec(
                        "workspace",
                        "Research workspace name",
                        ("--workspace",),
                        default="default",
                        sensitive=True,
                        completion=CompletionKind.WORKSPACE,
                    ),
                ),
            ),
            ActionSpec(
                "add",
                "Add a research person",
                (
                    ArgumentSpec(
                        "display_name",
                        "Person display name",
                        sensitive=True,
                        completion=CompletionKind.PERSON,
                    ),
                    ArgumentSpec(
                        "living_status",
                        "Living status",
                        ("--living-status",),
                        default="unknown",
                        choices=("living", "deceased", "possibly_living", "unknown"),
                        sensitive=True,
                        completion=CompletionKind.CHOICES,
                    ),
                    ArgumentSpec(
                        "notes", "Research notes", ("--notes",), default="", sensitive=True
                    ),
                    ArgumentSpec(
                        "workspace",
                        "Research workspace name",
                        ("--workspace",),
                        default="default",
                        sensitive=True,
                        completion=CompletionKind.WORKSPACE,
                    ),
                ),
            ),
        ),
    ),
    "providers": CommandSpec(
        "providers",
        "Provider and consent profiles",
        (
            ActionSpec("list", "List provider profiles and consent grants"),
            ActionSpec(
                "create",
                "Create a provider profile",
                (
                    ArgumentSpec(
                        "name", "Provider profile name", completion=CompletionKind.PROFILE
                    ),
                    ArgumentSpec(
                        "provider",
                        "Provider identifier",
                        ("--provider",),
                        choices=_PROVIDERS,
                        required=True,
                        completion=CompletionKind.CHOICES,
                    ),
                    ArgumentSpec(
                        "model",
                        "Provider model identifier",
                        ("--model",),
                        required=True,
                        completion=CompletionKind.MODEL,
                    ),
                ),
            ),
            ActionSpec(
                "consent",
                "Create a scoped consent grant",
                (
                    ArgumentSpec("name", "Consent grant name", completion=CompletionKind.CONSENT),
                    ArgumentSpec(
                        "profile",
                        "Provider profile name",
                        ("--profile",),
                        required=True,
                        completion=CompletionKind.PROFILE,
                    ),
                    ArgumentSpec(
                        "module",
                        "Allowed module identifier",
                        ("--module",),
                        required=True,
                        action=ArgumentAction.APPEND,
                        completion=CompletionKind.MODULE,
                    ),
                    ArgumentSpec(
                        "purpose",
                        "Allowed processing purpose",
                        ("--purpose",),
                        required=True,
                        action=ArgumentAction.APPEND,
                        sensitive=True,
                    ),
                    ArgumentSpec(
                        "data_class",
                        "Allowed data classification",
                        ("--data-class",),
                        choices=(
                            "public_genealogy",
                            "deceased_person",
                            "living_person",
                            "possibly_living_person",
                            "free_text_note",
                            "source_transcription",
                            "government_identifier",
                        ),
                        required=True,
                        action=ArgumentAction.APPEND,
                        completion=CompletionKind.CHOICES,
                    ),
                    ArgumentSpec(
                        "model",
                        "Allowed provider model",
                        ("--model",),
                        required=True,
                        action=ArgumentAction.APPEND,
                        completion=CompletionKind.MODEL,
                    ),
                    ArgumentSpec(
                        "max_cost_usd",
                        "Maximum allowed request cost in USD",
                        ("--max-cost-usd",),
                        ArgumentType.NUMBER,
                    ),
                    ArgumentSpec(
                        "retain_payloads",
                        "Allow provider payload retention",
                        ("--retain-payloads",),
                        default=False,
                        action=ArgumentAction.STORE_TRUE,
                    ),
                ),
            ),
            ActionSpec(
                "revoke",
                "Revoke a consent grant",
                (ArgumentSpec("name", "Consent grant name", completion=CompletionKind.CONSENT),),
            ),
        ),
    ),
    "secrets": CommandSpec(
        "secrets",
        "OS-keyring secret references",
        (
            ActionSpec(
                "set",
                "Set a secret value using a no-echo prompt",
                (
                    ArgumentSpec(
                        "name",
                        "Secret reference name",
                        completion=CompletionKind.KEYRING_REFERENCE,
                    ),
                ),
            ),
            ActionSpec(
                "delete",
                "Delete a secret reference",
                (
                    ArgumentSpec(
                        "name",
                        "Secret reference name",
                        completion=CompletionKind.KEYRING_REFERENCE,
                    ),
                ),
            ),
            ActionSpec(
                "status",
                "Show secret reference status",
                (
                    ArgumentSpec(
                        "name",
                        "Optional secret reference name",
                        cardinality=ArgumentCardinality.OPTIONAL,
                        completion=CompletionKind.KEYRING_REFERENCE,
                    ),
                ),
            ),
        ),
    ),
    "ocr": CommandSpec(
        "ocr",
        "Structured OCR extraction",
        (
            ActionSpec(
                "extract",
                "Extract structured genealogy data from OCR text",
                (
                    ArgumentSpec(
                        "input",
                        "OCR text input path",
                        ("--input",),
                        ArgumentType.PATH,
                        required=True,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                    ArgumentSpec(
                        "provider",
                        "Provider profile identifier",
                        ("--provider",),
                        required=True,
                        completion=CompletionKind.PROVIDER,
                    ),
                    ArgumentSpec(
                        "model",
                        "Provider model identifier",
                        ("--model",),
                        required=True,
                        completion=CompletionKind.MODEL,
                    ),
                    ArgumentSpec(
                        "consent",
                        "Consent grant name",
                        ("--consent",),
                        completion=CompletionKind.CONSENT,
                    ),
                ),
            ),
        ),
    ),
    "database": CommandSpec(
        "database",
        "Encrypted workspace maintenance",
        (
            ActionSpec(
                "backup",
                "Create an encrypted database backup",
                (
                    ArgumentSpec(
                        "destination",
                        "Encrypted backup destination",
                        value_type=ArgumentType.PATH,
                        sensitive=True,
                        completion=CompletionKind.FILE,
                    ),
                ),
            ),
            ActionSpec("diagnose", "Run read-only SQLCipher and credential-store checks"),
        ),
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

    def load(self) -> list[ModuleDescriptor]:
        """Return enabled descriptors without importing obsolete console adapters."""

        return self.descriptors()

    def enable(self, module_id: str) -> None:
        if module_id not in BUILTIN_MODULES:
            raise KeyError(module_id)
        self.context.config.enabled_modules.add(module_id)
        self.context.config.save()

    def disable(self, module_id: str) -> None:
        self.context.config.enabled_modules.discard(module_id)
        self.context.config.save()
