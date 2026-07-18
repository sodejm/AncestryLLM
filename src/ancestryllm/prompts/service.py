"""Version prompts and render only declared variables without code execution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from string import Template
from typing import Any

from ancestryllm.core.errors import AncestryError
from ancestryllm.storage.database import Database
from ancestryllm.storage.repositories import PromptRepository

VARIABLE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class SavedPrompt:
    name: str
    purpose: str
    version: int
    body: str
    variables: tuple[str, ...]
    response_schema: dict[str, Any] | None


class PromptService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        name: str,
        purpose: str,
        body: str,
        variables: list[str],
        response_schema: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> SavedPrompt:
        if not name.strip() or not purpose.strip() or not body.strip():
            raise AncestryError("PROMPT_INVALID", "Prompt name, purpose, and body are required.")
        invalid = [value for value in variables if not VARIABLE_NAME.fullmatch(value)]
        if invalid:
            raise AncestryError(
                "PROMPT_VARIABLE_INVALID",
                "Prompt variable names must be simple identifiers.",
                details={"invalid": invalid},
            )
        # Template.pattern tuples contain named/braced captures; extract actual identifiers safely.
        discovered = {
            match.group("named") or match.group("braced")
            for match in Template.pattern.finditer(body)
            if match.group("named") or match.group("braced")
        }
        if discovered != set(variables):
            raise AncestryError(
                "PROMPT_VARIABLE_MISMATCH",
                "Declared prompt variables do not match the template placeholders.",
                details={"declared": sorted(variables), "referenced": sorted(discovered)},
            )
        with self.database.session() as session:
            model = PromptRepository(session).save_version(
                name, purpose, body, variables, response_schema, tags
            )
        return SavedPrompt(
            name, purpose, model.version, body, tuple(sorted(variables)), response_schema
        )

    def get(self, name: str, version: int | None = None) -> SavedPrompt:
        with self.database.session() as session:
            result = PromptRepository(session).get(name, version)
            if result is None:
                raise AncestryError("PROMPT_NOT_FOUND", f"Prompt not found: {name}")
            template, selected = result
            return SavedPrompt(
                template.name,
                template.purpose,
                selected.version,
                selected.body,
                tuple(json.loads(selected.variables_json)),
                json.loads(selected.response_schema_json)
                if selected.response_schema_json
                else None,
            )

    def render(self, name: str, values: dict[str, str], version: int | None = None) -> str:
        prompt = self.get(name, version)
        expected = set(prompt.variables)
        supplied = set(values)
        if expected != supplied:
            raise AncestryError(
                "PROMPT_RENDER_VARIABLES",
                "Prompt rendering requires exactly the declared variables.",
                details={
                    "missing": sorted(expected - supplied),
                    "unexpected": sorted(supplied - expected),
                },
            )
        return Template(prompt.body).substitute(values)

    def list(self) -> list[tuple[str, str]]:
        with self.database.session() as session:
            return [
                (item.name, item.purpose) for item in PromptRepository(session).list_templates()
            ]
