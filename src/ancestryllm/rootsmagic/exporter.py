"""Deterministic, schema-adaptive RootsMagic-to-GEDCOM export."""

from __future__ import annotations

import os
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ancestryllm.core.errors import AncestryError, FileIngressError
from ancestryllm.core.ingress import FileKind
from ancestryllm.core.publication import (
    StagedFileToken,
    claim_staged_path,
    cleanup_staged_path,
    paths_alias,
    publish_staged_bundle,
    staging_path,
    write_staged_text,
)
from ancestryllm.gedcom.engine import validate_gedcom_555
from ancestryllm.rootsmagic.reader import RootsMagicReader


def _value(row: dict[str, Any], *names: str, default: Any = "") -> Any:
    lowered = {key.casefold(): value for key, value in row.items()}
    for name in names:
        if name.casefold() in lowered and lowered[name.casefold()] is not None:
            return lowered[name.casefold()]
    return default


def _clean_text(value: Any) -> str:
    if isinstance(value, bytes):
        return ""
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _tag_name(column: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", column).upper()
    return ("_RM_" + clean)[:31]


def _paths_nested(left: Path, right: Path) -> bool:
    """Fail closed when either output pathname is an ancestor of the other."""

    try:
        left_parts = tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in left.resolve(strict=False).parts
        )
        right_parts = tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in right.resolve(strict=False).parts
        )
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return True
    return (
        len(left_parts) < len(right_parts) and right_parts[: len(left_parts)] == left_parts
    ) or (len(right_parts) < len(left_parts) and left_parts[: len(right_parts)] == right_parts)


@dataclass(slots=True)
class ExportReport:
    profile: str
    destination: str
    people_read: int
    people_written: int
    families_written: int
    living_omitted: int
    mapped_tables: list[str] = field(default_factory=list)
    unmapped_tables: list[str] = field(default_factory=list)
    unmapped_columns: dict[str, list[str]] = field(default_factory=dict)

    def markdown(self, source: Path, output: Path) -> str:
        lines = [
            "# RootsMagic GEDCOM Export Report",
            "",
            f"- Source: `{source.name}`",
            f"- Output: `{output.name}`",
            f"- Profile: `{self.profile}`",
            f"- Destination check: `{self.destination}`",
            f"- People read/written: {self.people_read}/{self.people_written}",
            f"- Families written: {self.families_written}",
            f"- Living people omitted: {self.living_omitted}",
            "",
            "## Mapped tables",
            "",
        ]
        lines.extend(f"- `{name}`" for name in self.mapped_tables or ["None"])
        lines.extend(["", "## Unmapped data", ""])
        lines.extend(f"- Table `{name}`" for name in self.unmapped_tables)
        for table, columns in sorted(self.unmapped_columns.items()):
            lines.append(f"- `{table}` columns: " + ", ".join(f"`{item}`" for item in columns))
        lines.extend(
            [
                "",
                "Portable exports omit unmapped fields. Preservation exports retain safely attributable",
                "scalar PersonTable values as `_RM_*` custom tags; binary and unattached records remain report-only.",
                "Manual importer smoke testing is required before claiming destination interoperability.",
            ]
        )
        return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class RootsMagicExportResult:
    output_path: Path
    report_path: Path
    report: ExportReport


class RootsMagicExporter:
    def __init__(self, reader: RootsMagicReader) -> None:
        self.reader = reader

    @staticmethod
    def _source_changed(exc: FileIngressError) -> AncestryError:
        return AncestryError(
            "ROOTSMAGIC_FILE_CHANGED",
            "The RootsMagic database changed during export; outputs were discarded.",
            "Close RootsMagic and export again from a stable backup.",
            exit_code=2,
        )

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> StagedFileToken:
        return write_staged_text(path, payload)

    @staticmethod
    def _scope_people(
        root: str | None,
        scope: str,
        generations: int | None,
        families: list[dict[str, Any]],
        children: list[dict[str, Any]],
    ) -> set[str] | None:
        if root is None:
            return None
        parent_to_children: dict[str, set[str]] = defaultdict(set)
        child_to_parents: dict[str, set[str]] = defaultdict(set)
        family_members: dict[str, set[str]] = defaultdict(set)
        family_parents: dict[str, set[str]] = defaultdict(set)
        for family in families:
            family_id = str(_value(family, "FamilyID", "ID"))
            for parent in (_value(family, "FatherID"), _value(family, "MotherID")):
                if str(parent) not in {"", "0", "None"}:
                    family_parents[family_id].add(str(parent))
                    family_members[family_id].add(str(parent))
        for child in children:
            family_id = str(_value(child, "FamilyID"))
            child_id = str(_value(child, "ChildID", "PersonID"))
            if child_id in {"", "0", "None"}:
                continue
            family_members[family_id].add(child_id)
            for parent in family_parents.get(family_id, set()):
                parent_to_children[parent].add(child_id)
                child_to_parents[child_id].add(parent)
        if scope == "connected":
            adjacency: dict[str, set[str]] = defaultdict(set)
            for members in family_members.values():
                for member in members:
                    adjacency[member].update(members - {member})
        elif scope == "ancestors":
            adjacency = child_to_parents
        elif scope == "descendants":
            adjacency = parent_to_children
        else:
            raise AncestryError("EXPORT_SCOPE_INVALID", f"Unknown subtree scope: {scope}")
        seen = {root}
        pending: deque[tuple[str, int]] = deque([(root, 0)])
        while pending:
            person, depth = pending.popleft()
            if generations is not None and depth >= generations:
                continue
            for related in adjacency.get(person, set()):
                if related not in seen:
                    seen.add(related)
                    pending.append((related, depth + 1))
        return seen

    def export(
        self,
        tree: Path,
        output: Path,
        *,
        profile: str = "portable",
        gedcom_version: str = "5.5.5",
        destination: str = "generic",
        root_person_id: str | None = None,
        scope: str = "connected",
        generations: int | None = None,
        living: str = "exclude",
        report_path: Path | None = None,
    ) -> RootsMagicExportResult:
        if profile not in {"portable", "preservation"}:
            raise AncestryError(
                "EXPORT_PROFILE_INVALID", "Profile must be portable or preservation."
            )
        if gedcom_version not in {"5.5.5", "5.5.1"}:
            raise AncestryError("GEDCOM_VERSION_INVALID", "GEDCOM version must be 5.5.5 or 5.5.1.")
        if destination not in {"generic", "ancestry", "geni", "myheritage"}:
            raise AncestryError("EXPORT_DESTINATION_INVALID", "Unsupported destination profile.")
        if living not in {"exclude", "redact", "include"}:
            raise AncestryError(
                "EXPORT_LIVING_INVALID", "Living policy must be exclude, redact, or include."
            )
        source_tree = self.reader.ingress.normalize_path(
            tree,
            FileKind.ROOTSMAGIC,
            absolute=True,
        )
        resolved_output = self.reader.ingress.normalize_path(
            output,
            FileKind.ROOTSMAGIC,
            resolve=True,
        )
        resolved_report = self.reader.ingress.normalize_path(
            report_path or resolved_output.with_suffix(".export.md"),
            FileKind.ROOTSMAGIC,
            resolve=True,
        )
        if paths_alias(source_tree, resolved_output):
            raise AncestryError(
                "EXPORT_OVERWRITE_INPUT", "Output must not overwrite RootsMagic data."
            )
        if (
            paths_alias(resolved_report, source_tree)
            or paths_alias(resolved_report, resolved_output)
            or _paths_nested(resolved_report, resolved_output)
        ):
            raise AncestryError(
                "EXPORT_REPORT_ALIAS",
                "The export report must not alias the source database or primary output.",
            )
        if not resolved_output.parent.is_dir() or not resolved_report.parent.is_dir():
            raise AncestryError(
                "EXPORT_OUTPUT_DIRECTORY_INVALID",
                "Export output and report parent directories must already exist.",
                "Create both local parent directories, then retry the export.",
                exit_code=2,
            )
        try:
            fingerprint = self.reader.fingerprint_source(source_tree)
            with self.reader.operation(source_tree, fingerprint) as schema:
                people_rows = self.reader.read_table(source_tree, "PersonTable")
                name_rows = self.reader.read_table(source_tree, "NameTable")
                family_rows = self.reader.read_table(source_tree, "FamilyTable")
                child_rows = self.reader.read_table(source_tree, "ChildTable")
        except FileIngressError as exc:
            if exc.code != "FILE_INPUT_CHANGED":
                raise
            raise self._source_changed(exc) from exc
        if not people_rows:
            raise AncestryError(
                "ROOTSMAGIC_SCHEMA_UNSUPPORTED",
                "PersonTable is missing or empty; a safe GEDCOM cannot be produced.",
            )
        included = self._scope_people(root_person_id, scope, generations, family_rows, child_rows)
        names_by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in name_rows:
            names_by_person[str(_value(row, "OwnerID", "PersonID"))].append(row)
        person_map: dict[str, str] = {}
        living_ids: set[str] = set()
        selected_rows: list[dict[str, Any]] = []
        for row in people_rows:
            person_id = str(_value(row, "PersonID", "ID"))
            if included is not None and person_id not in included:
                continue
            is_living = str(_value(row, "Living", "IsLiving", default="0")).casefold() in {
                "1",
                "true",
                "yes",
            }
            if is_living:
                living_ids.add(person_id)
                if living == "exclude":
                    continue
            selected_rows.append(row)
        for index, row in enumerate(
            sorted(selected_rows, key=lambda item: str(_value(item, "PersonID", "ID"))), 1
        ):
            person_map[str(_value(row, "PersonID", "ID"))] = f"@I{index}@"

        lines = [
            "0 HEAD",
            "1 SOUR AncestryLLM",
            "2 VERS 0.2.0",
            "1 GEDC",
            f"2 VERS {gedcom_version}",
            "2 FORM LINEAGE-LINKED",
            "1 CHAR UTF-8",
            "1 SUBM @U1@",
            "0 @U1@ SUBM",
            "1 NAME AncestryLLM Local Export",
        ]
        known_person_columns = {"personid", "id", "sex", "living", "isliving"}
        unmapped_person_columns: set[str] = set()
        for row in selected_rows:
            person_id = str(_value(row, "PersonID", "ID"))
            pointer = person_map[person_id]
            lines.append(f"0 {pointer} INDI")
            names = names_by_person.get(person_id, [])
            primary = next(
                (
                    item
                    for item in names
                    if str(_value(item, "IsPrimary", default="0")) in {"1", "True", "true"}
                ),
                names[0] if names else {},
            )
            given = _clean_text(_value(primary, "Given", "GivenName"))
            surname = _clean_text(_value(primary, "Surname", "LastName"))
            if person_id in living_ids and living == "redact":
                given, surname = "Living", "Private"
            lines.append(
                f"1 NAME {given} /{surname}/" if surname else f"1 NAME {given or 'Unknown'}"
            )
            raw_sex = _clean_text(_value(row, "Sex", "Gender")).upper()
            sex = {"0": "M", "1": "F", "2": "U"}.get(raw_sex, raw_sex[:1])
            if sex in {"M", "F", "U", "X"}:
                lines.append(f"1 SEX {sex}")
            if profile == "preservation":
                for column, raw in sorted(row.items()):
                    if (
                        column.casefold() in known_person_columns
                        or raw in {None, "", 0}
                        or isinstance(raw, bytes)
                    ):
                        continue
                    lines.append(f"1 {_tag_name(column)} {_clean_text(raw)}")
                    unmapped_person_columns.add(column)

        families_written = 0
        children_by_family: dict[str, list[str]] = defaultdict(list)
        for row in child_rows:
            child_id = str(_value(row, "ChildID", "PersonID"))
            if child_id in person_map:
                children_by_family[str(_value(row, "FamilyID"))].append(child_id)
        for row in family_rows:
            family_id = str(_value(row, "FamilyID", "ID"))
            father = str(_value(row, "FatherID"))
            mother = str(_value(row, "MotherID"))
            children = children_by_family.get(family_id, [])
            if father not in person_map and mother not in person_map and not children:
                continue
            families_written += 1
            lines.append(f"0 @F{families_written}@ FAM")
            if father in person_map:
                lines.append(f"1 HUSB {person_map[father]}")
            if mother in person_map:
                lines.append(f"1 WIFE {person_map[mother]}")
            lines.extend(f"1 CHIL {person_map[child]}" for child in children)
        lines.append("0 TRLR")
        if gedcom_version == "5.5.5":
            validate_gedcom_555(lines)

        mapped = [
            name
            for name in ("PersonTable", "NameTable", "FamilyTable", "ChildTable")
            if name in schema
        ]
        unmapped = [name for name in schema if name not in mapped]
        report = ExportReport(
            profile=profile,
            destination=destination,
            people_read=len(people_rows),
            people_written=len(selected_rows),
            families_written=families_written,
            living_omitted=len(living_ids) if living == "exclude" else 0,
            mapped_tables=mapped,
            unmapped_tables=unmapped,
            unmapped_columns={"PersonTable": sorted(unmapped_person_columns)}
            if unmapped_person_columns
            else {},
        )
        staged_output: Path | None = None
        staged_report: Path | None = None
        try:
            staged_output = staging_path(resolved_output)
            staged_report = staging_path(resolved_report)
            output_token = self._atomic_write(staged_output, "\n".join(lines) + "\n")
            claim_staged_path(staged_output, output_token)
            report_token = self._atomic_write(
                staged_report,
                report.markdown(source_tree, resolved_output),
            )
            claim_staged_path(staged_report, report_token)

            def validate_source() -> None:
                try:
                    self.reader.verify_source(source_tree, fingerprint)
                except FileIngressError as exc:
                    raise self._source_changed(exc) from exc

            publish_staged_bundle(
                (
                    (staged_output, resolved_output),
                    (staged_report, resolved_report),
                ),
                replace=os.replace,
                validate_after=validate_source,
            )
        except BaseException:
            if staged_output is not None:
                cleanup_staged_path(staged_output)
            if staged_report is not None:
                cleanup_staged_path(staged_report)
            raise
        return RootsMagicExportResult(resolved_output, resolved_report, report)
