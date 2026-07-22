"""Unified one-shot command line and entry point for the interactive console."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from ancestryllm.console.presentation import PresentationAdapter
from ancestryllm.core.config import AppConfig
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.modules import (
    COMMAND_SPECIFICATIONS,
    GLOBAL_ARGUMENTS,
    ActionSpec,
    ArgumentAction,
    ArgumentCardinality,
    ArgumentSpec,
    ArgumentType,
    ModuleDescriptor,
    ModuleRegistry,
)
from ancestryllm.domain.models import LivingStatus
from ancestryllm.llm.contracts import DataClass
from ancestryllm.llm.policy import ConsentGrant

_ARGUMENT_TYPES: dict[ArgumentType, type[str] | type[int] | type[float] | type[Path]] = {
    ArgumentType.STRING: str,
    ArgumentType.INTEGER: int,
    ArgumentType.NUMBER: float,
    ArgumentType.PATH: Path,
}

_ARGUMENT_CARDINALITIES: dict[ArgumentCardinality, str] = {
    ArgumentCardinality.OPTIONAL: "?",
    ArgumentCardinality.ONE_OR_MORE: "+",
    ArgumentCardinality.REMAINDER: argparse.REMAINDER,
}


def _add_argument(target: Any, specification: ArgumentSpec) -> None:
    names = specification.flags or (specification.name,)
    options: dict[str, Any] = {"help": specification.help}
    if specification.action is not ArgumentAction.STORE:
        options["action"] = specification.action.value
    else:
        options["type"] = _ARGUMENT_TYPES[specification.value_type]
    if specification.required and specification.flags:
        options["required"] = True
    if specification.default is not None or specification.action is ArgumentAction.STORE_TRUE:
        options["default"] = (
            list(specification.default)
            if isinstance(specification.default, tuple)
            else specification.default
        )
    if specification.choices:
        options["choices"] = specification.choices
    if specification.cardinality is not None:
        options["nargs"] = _ARGUMENT_CARDINALITIES[specification.cardinality]
    if specification.metavar is not None:
        options["metavar"] = specification.metavar
    target.add_argument(*names, **options)


def _add_action_arguments(parser: argparse.ArgumentParser, specification: ActionSpec) -> None:
    grouped_arguments: dict[str, Any] = {}
    for group in specification.exclusive_groups:
        target = parser.add_mutually_exclusive_group(required=group.required)
        for argument_name in group.arguments:
            grouped_arguments[argument_name] = target
    for argument in specification.arguments:
        _add_argument(grouped_arguments.get(argument.name, parser), argument)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ancestry", description=__doc__)
    for argument in GLOBAL_ARGUMENTS:
        _add_argument(parser, argument)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in COMMAND_SPECIFICATIONS.values():
        command_parser = commands.add_parser(command.name, help=command.help)
        actions = command_parser.add_subparsers(dest="action", required=True)
        for action in command.actions:
            action_parser = actions.add_parser(action.name, help=action.help)
            _add_action_arguments(action_parser, action)
    return parser


def _descriptor_payload(descriptor: ModuleDescriptor) -> dict[str, Any]:
    """Preserve the established modules-list JSON contract."""

    return {
        "module_id": descriptor.module_id,
        "name": descriptor.name,
        "summary": descriptor.summary,
        "actions": descriptor.actions,
        "implementation": descriptor.implementation,
        "configuration": descriptor.configuration,
        "required_services": descriptor.required_services,
    }


def _emit(value: Any, json_output: bool = False) -> None:
    PresentationAdapter().render(value, json_output=json_output)


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
            _emit([_descriptor_payload(item) for item in registry.descriptors()], json_output)
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
        if args.action == "diagnose":
            from ancestryllm.storage.diagnostics import diagnose_storage

            _emit(diagnose_storage(context.database.path, context.secrets), json_output)
            return 0
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
        from ancestryllm.console.shell import run_repl

        return run_repl(context)
    parser = build_parser()
    try:
        args = parser.parse_args(arguments)
        selected_context = context or AppContext.build(
            AppConfig.load(args.config) if args.config else None
        )
        return dispatch(args, selected_context)
    except AncestryError as exc:
        PresentationAdapter.for_file(sys.stderr).render_error(exc)
        return exc.exit_code
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[INPUT_ERROR] {exc}", file=sys.stderr)
        return 2
