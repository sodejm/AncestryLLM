"""Unified one-shot command line and entry point for the interactive console."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Sequence, cast

from ancestryllm.core.config import AppConfig
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.modules import BUILTIN_MODULES, ModuleRegistry
from ancestryllm.domain.models import LivingStatus
from ancestryllm.llm.contracts import DataClass
from ancestryllm.llm.policy import ConsentGrant


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ancestry", description=__doc__)
    parser.add_argument("--config", type=Path, help="Explicit non-secret config.toml path")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    modules = commands.add_parser("modules", help="List or configure built-in modules")
    module_actions = modules.add_subparsers(dest="action", required=True)
    module_actions.add_parser("list")
    for action in ("enable", "disable"):
        child = module_actions.add_parser(action)
        child.add_argument("module_id", choices=sorted(BUILTIN_MODULES))

    rootsmagic = commands.add_parser("rootsmagic", help="Immutable RootsMagic operations")
    rm_actions = rootsmagic.add_subparsers(dest="action", required=True)
    rm_actions.add_parser("list")
    rm_query = rm_actions.add_parser("query")
    rm_query.add_argument("--tree", required=True)
    query_mode = rm_query.add_mutually_exclusive_group(required=True)
    query_mode.add_argument("--sql")
    query_mode.add_argument("--question")
    rm_query.add_argument("--provider", default="none")
    rm_query.add_argument("--model", default="")
    rm_query.add_argument("--consent")
    rm_export = rm_actions.add_parser("export")
    rm_export.add_argument("--tree", required=True)
    rm_export.add_argument("--output", required=True, type=Path)
    rm_export.add_argument("--profile", choices=("portable", "preservation"), default="portable")
    rm_export.add_argument("--gedcom-version", choices=("5.5.5", "5.5.1"), default="5.5.5")
    rm_export.add_argument(
        "--destination", choices=("generic", "ancestry", "geni", "myheritage"), default="generic"
    )
    rm_export.add_argument("--root-person-id")
    rm_export.add_argument(
        "--scope", choices=("connected", "ancestors", "descendants"), default="connected"
    )
    rm_export.add_argument("--generations", type=int)
    rm_export.add_argument("--living", choices=("exclude", "redact", "include"), default="exclude")
    rm_export.add_argument("--report", type=Path)

    gedcom = commands.add_parser("gedcom", help="GEDCOM operations")
    gedcom_actions = gedcom.add_subparsers(dest="action", required=True)
    merge = gedcom_actions.add_parser("merge")
    merge.add_argument("inputs", nargs="+", type=Path)
    merge.add_argument("--output", "-o", required=True, type=Path)
    merge.add_argument("--root-person")
    merge.add_argument("--quality-report", type=Path)
    merge.add_argument("--gedcom-version", choices=("5.5.5", "5.5.1"), default="5.5.5")
    merge.add_argument("--provider", default="none")
    merge.add_argument("--model", default="")
    merge.add_argument("--consent")
    merge.add_argument("--similarity-threshold", type=int, default=78)
    subtree = gedcom_actions.add_parser("subtree")
    subtree.add_argument("input", type=Path)
    subtree.add_argument("--output", "-o", required=True, type=Path)
    subtree.add_argument("--root-person", required=True)
    subtree.add_argument(
        "--scope", choices=("connected", "ancestors", "descendants"), default="connected"
    )
    subtree.add_argument("--generations", type=int)
    subtree.add_argument("--gedcom-version", choices=("5.5.5", "5.5.1"), default="5.5.5")
    quality = gedcom_actions.add_parser("quality")
    quality.add_argument("input", type=Path)
    quality.add_argument("--output", "-o", required=True, type=Path)
    quality.add_argument("--root-person", required=True)
    sync = gedcom_actions.add_parser("sync")
    sync.add_argument("sync_command", choices=("update", "rebase"))
    sync.add_argument("sync_args", nargs=argparse.REMAINDER)

    prompts = commands.add_parser("prompts", help="Versioned saved prompts")
    prompt_actions = prompts.add_subparsers(dest="action", required=True)
    prompt_actions.add_parser("list")
    prompt_save = prompt_actions.add_parser("save")
    prompt_save.add_argument("name")
    prompt_save.add_argument("--purpose", required=True)
    body = prompt_save.add_mutually_exclusive_group(required=True)
    body.add_argument("--body")
    body.add_argument("--body-file", type=Path)
    prompt_save.add_argument("--variable", action="append", default=[])
    prompt_save.add_argument("--schema-file", type=Path)
    prompt_save.add_argument("--tag", action="append", default=[])
    prompt_show = prompt_actions.add_parser("show")
    prompt_show.add_argument("name")
    prompt_show.add_argument("--version", type=int)
    prompt_render = prompt_actions.add_parser("render")
    prompt_render.add_argument("name")
    prompt_render.add_argument("--version", type=int)
    prompt_render.add_argument("--value", action="append", default=[], metavar="NAME=VALUE")

    people = commands.add_parser("people", help="Encrypted research workspace")
    people_actions = people.add_subparsers(dest="action", required=True)
    people_list = people_actions.add_parser("list")
    people_list.add_argument("--workspace", default="default")
    people_add = people_actions.add_parser("add")
    people_add.add_argument("display_name")
    people_add.add_argument(
        "--living-status", choices=tuple(item.value for item in LivingStatus), default="unknown"
    )
    people_add.add_argument("--notes", default="")
    people_add.add_argument("--workspace", default="default")

    providers = commands.add_parser("providers", help="Provider and consent profiles")
    provider_actions = providers.add_subparsers(dest="action", required=True)
    provider_actions.add_parser("list")
    profile = provider_actions.add_parser("create")
    profile.add_argument("name")
    profile.add_argument(
        "--provider",
        required=True,
        choices=("ollama", "openai", "anthropic", "gemini", "openrouter"),
    )
    profile.add_argument("--model", required=True)
    consent = provider_actions.add_parser("consent")
    consent.add_argument("name")
    consent.add_argument("--profile", required=True)
    consent.add_argument("--module", action="append", required=True)
    consent.add_argument("--purpose", action="append", required=True)
    consent.add_argument(
        "--data-class",
        action="append",
        choices=tuple(item.value for item in DataClass),
        required=True,
    )
    consent.add_argument("--model", action="append", required=True)
    consent.add_argument("--max-cost-usd", type=float)
    consent.add_argument("--retain-payloads", action="store_true")
    revoke = provider_actions.add_parser("revoke")
    revoke.add_argument("name")

    secrets_parser = commands.add_parser("secrets", help="OS-keyring secret references")
    secret_actions = secrets_parser.add_subparsers(dest="action", required=True)
    secret_set = secret_actions.add_parser("set")
    secret_set.add_argument("name")
    secret_delete = secret_actions.add_parser("delete")
    secret_delete.add_argument("name")
    secret_status = secret_actions.add_parser("status")
    secret_status.add_argument("name", nargs="?")

    ocr = commands.add_parser("ocr", help="Structured OCR extraction")
    ocr_actions = ocr.add_subparsers(dest="action", required=True)
    extract = ocr_actions.add_parser("extract")
    extract.add_argument("--input", required=True, type=Path)
    extract.add_argument("--provider", required=True)
    extract.add_argument("--model", required=True)
    extract.add_argument("--consent")

    database = commands.add_parser("database", help="Encrypted workspace maintenance")
    db_actions = database.add_subparsers(dest="action", required=True)
    backup = db_actions.add_parser("backup")
    backup.add_argument("destination", type=Path)
    return parser


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _plain(item) for key, item in asdict(cast(Any, value)).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    if hasattr(value, "__table__"):
        return {
            column.name: _plain(getattr(value, column.name)) for column in value.__table__.columns
        }
    return value


def _emit(value: Any, json_output: bool = False) -> None:
    plain = _plain(value)
    if json_output:
        print(json.dumps(plain, indent=2, sort_keys=True))
    elif isinstance(plain, str):
        print(plain)
    elif isinstance(plain, list):
        for item in plain:
            print(item if isinstance(item, str) else json.dumps(item, sort_keys=True))
    else:
        print(json.dumps(plain, indent=2, sort_keys=True))


def _consent(context: AppContext, name: str | None) -> ConsentGrant | None:
    return context.provider_profiles.consent_grant(name) if name else None


def _key_values(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise AncestryError("ARGUMENT_INVALID", f"Expected NAME=VALUE, received {raw!r}.")
        name, value = raw.split("=", 1)
        result[name] = value
    return result


def dispatch(args: argparse.Namespace, context: AppContext) -> int:
    json_output = bool(args.json)
    if args.command == "modules":
        registry = ModuleRegistry(context)
        if args.action == "list":
            _emit([asdict(item) for item in registry.descriptors()], json_output)
        elif args.action == "enable":
            registry.enable(args.module_id)
            _emit(f"Enabled module: {args.module_id}", json_output)
        else:
            registry.disable(args.module_id)
            _emit(f"Disabled module: {args.module_id}", json_output)
        return 0

    if args.command == "rootsmagic":
        from ancestryllm.rootsmagic.service import RootsMagicService

        rootsmagic_service = RootsMagicService(context.config, context.llm)
        if args.action == "list":
            _emit(rootsmagic_service.list_trees(), json_output)
        elif args.action == "query":
            query_result = (
                rootsmagic_service.query_sql(args.tree, args.sql)
                if args.sql is not None
                else rootsmagic_service.query_question(
                    args.tree,
                    args.question,
                    provider_id=args.provider,
                    model=args.model,
                    consent=_consent(context, args.consent),
                )
            )
            _emit(query_result, json_output)
        else:
            export_result = rootsmagic_service.export(
                args.tree,
                args.output,
                profile=args.profile,
                gedcom_version=args.gedcom_version,
                destination=args.destination,
                root_person_id=args.root_person_id,
                scope=args.scope,
                generations=args.generations,
                living=args.living,
                report_path=args.report,
            )
            _emit(export_result, json_output)
        return 0

    if args.command == "gedcom":
        from ancestryllm.gedcom.service import GedcomService

        gedcom_service = GedcomService(context.llm)
        if args.action == "merge":
            gedcom_result = gedcom_service.merge(
                args.inputs,
                args.output,
                root_person=args.root_person,
                quality_path=args.quality_report,
                gedcom_version=args.gedcom_version,
                provider_id=args.provider,
                model=args.model,
                consent=_consent(context, args.consent),
                threshold=args.similarity_threshold,
            )
            _emit(gedcom_result, json_output)
        elif args.action == "subtree":
            _emit(
                gedcom_service.subtree(
                    args.input,
                    args.output,
                    root_person=args.root_person,
                    scope=args.scope,
                    generations=args.generations,
                    gedcom_version=args.gedcom_version,
                ),
                json_output,
            )
        elif args.action == "quality":
            _emit(
                gedcom_service.quality(args.input, args.output, root_person=args.root_person),
                json_output,
            )
        else:
            return gedcom_service.sync([args.sync_command, *args.sync_args])
        return 0

    if args.command == "prompts":
        if args.action == "list":
            _emit(context.prompts.list(), json_output)
        elif args.action == "save":
            body = (
                args.body if args.body is not None else args.body_file.read_text(encoding="utf-8")
            )
            schema = (
                json.loads(args.schema_file.read_text(encoding="utf-8"))
                if args.schema_file
                else None
            )
            _emit(
                context.prompts.save(
                    args.name, args.purpose, body, args.variable, schema, args.tag
                ),
                json_output,
            )
        elif args.action == "show":
            _emit(context.prompts.get(args.name, args.version), json_output)
        else:
            _emit(
                context.prompts.render(args.name, _key_values(args.value), args.version),
                json_output,
            )
        return 0

    if args.command == "people":
        if args.action == "list":
            _emit(context.research.list_people(args.workspace), json_output)
        else:
            _emit(
                context.research.add_person(
                    args.display_name,
                    LivingStatus(args.living_status),
                    args.notes,
                    args.workspace,
                ),
                json_output,
            )
        return 0

    if args.command == "providers":
        if args.action == "list":
            _emit(
                {
                    "profiles": context.provider_profiles.list_profiles(),
                    "consents": context.provider_profiles.list_consents(),
                },
                json_output,
            )
        elif args.action == "create":
            _emit(
                context.provider_profiles.create_profile(args.name, args.provider, args.model),
                json_output,
            )
        elif args.action == "consent":
            _emit(
                context.provider_profiles.create_consent(
                    args.name,
                    args.profile,
                    modules=args.module,
                    purposes=args.purpose,
                    data_classes=[DataClass(value) for value in args.data_class],
                    models=args.model,
                    max_cost_usd=args.max_cost_usd,
                    retain_payloads=args.retain_payloads,
                ),
                json_output,
            )
        else:
            context.provider_profiles.revoke_consent(args.name)
            _emit(f"Revoked consent: {args.name}", json_output)
        return 0

    if args.command == "secrets":
        names = (
            [args.name]
            if args.name
            else [
                "openai.api_key",
                "anthropic.api_key",
                "gemini.api_key",
                "openrouter.api_key",
                "openrouter.management_key",
                "database.master_key",
            ]
        )
        if args.action == "set":
            value = getpass.getpass(f"Secret value for {args.name}: ")
            confirmation = getpass.getpass("Confirm secret value: ")
            if value != confirmation:
                raise AncestryError("SECRET_CONFIRMATION_FAILED", "Secret values did not match.")
            context.secrets.set(args.name, value)
            _emit(f"Stored secret reference: {args.name}", json_output)
        elif args.action == "delete":
            context.secrets.delete(args.name)
            _emit(f"Deleted secret reference: {args.name}", json_output)
        else:
            _emit({name: context.secrets.present(name) for name in names}, json_output)
        return 0

    if args.command == "ocr":
        from ancestryllm.ocr.service import OcrService

        if args.input.stat().st_size > 5_000_000:
            raise AncestryError("OCR_INPUT_TOO_LARGE", "OCR input exceeds the 5 MB local limit.")
        text = args.input.read_text(encoding="utf-8")
        ocr_result = OcrService(context.llm).extract(
            text,
            provider_id=args.provider,
            model=args.model,
            consent=_consent(context, args.consent),
        )
        _emit(ocr_result, json_output)
        return 0

    if args.command == "database":
        context.database.backup(args.destination.expanduser().resolve())
        _emit(f"Encrypted backup created: {args.destination}", json_output)
        return 0
    raise AncestryError("COMMAND_UNKNOWN", "Unknown command.")


def run_tokens(context: AppContext, tokens: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(list(tokens))
    return dispatch(args, context)


def main(argv: Sequence[str] | None = None, context: AppContext | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        from ancestryllm.console.app import AncestryConsole

        AncestryConsole(context or AppContext.build()).cmdloop()
        return 0
    parser = build_parser()
    try:
        args = parser.parse_args(arguments)
        selected_context = context or AppContext.build(
            AppConfig.load(args.config) if args.config else None
        )
        return dispatch(args, selected_context)
    except AncestryError as exc:
        print(exc.render(), file=sys.stderr)
        return exc.exit_code
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[INPUT_ERROR] {exc}", file=sys.stderr)
        return 2
