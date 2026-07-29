"""Offline behavior tests for GEDCOM merge quality analysis and reporting."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ancestryllm.core.errors import AncestryError, ProviderError
from ancestryllm.gedcom import engine as gm
from ancestryllm.gedcom.contracts import QualityResolution
from ancestryllm.gedcom.service import GedcomService

FIXTURES = Path(__file__).parent / "fixtures" / "gedcom_merge"
ADVERSARIAL_FIXTURES = Path(__file__).parent / "fixtures" / "gedcom_adversarial"
SOURCE_A = FIXTURES / "quality-source-a.ged"
SOURCE_B = FIXTURES / "quality-source-b.ged"
MALFORMED = FIXTURES / "malformed-rejected.ged"
PUBLIC_SOURCE_A = ADVERSARIAL_FIXTURES / "xref-source-a.ged"
PUBLIC_SOURCE_B = ADVERSARIAL_FIXTURES / "xref-source-b.ged"


def _loaded_fixture_tree() -> tuple[
    list[gm.IndividualRecord],
    list[gm.GedcomRecord],
    list[gm.ParsedSource],
    dict[str, str],
    list[gm.MergeDecision],
]:
    """Return the deterministically merged fictional fixture tree."""
    sources = gm.load_sources([SOURCE_A, SOURCE_B])
    source_records = [record for source in sources for record in source.records]
    people = [
        gm._individual_from_record(record) for record in source_records if record.tag == "INDI"
    ]
    people = gm.enrich_relationship_context(people, source_records)
    pointer_map: dict[str, str] = {}
    decisions: list[gm.MergeDecision] = []
    merged = gm.merge_records(
        people,
        auto=True,
        pointer_map=pointer_map,
        decisions=decisions,
    )
    return merged, source_records, sources, pointer_map, decisions


def _fixture_report() -> gm.QualityReport:
    """Build the deterministic fixture report used by focused tests."""
    people, records, sources, pointer_map, decisions = _loaded_fixture_tree()
    return gm.analyze_quality(
        people,
        records,
        sources,
        "@A_ROOT@",
        pointer_map=pointer_map,
        merge_decisions=decisions,
        output_file="master.ged",
    )


class TestTypedPersonalNames:
    """Preserve typed names and every standard subordinate name component."""

    def test_parses_case_insensitive_type_and_components(self) -> None:
        record = gm.GedcomRecord(
            [
                "0 @I1@ INDI",
                "1 NAME Dr. Ana /Stone/ Jr.",
                "2 type Married",
                "2 NPFX Dr.",
                "2 GIVN Ana",
                "2 NICK Annie",
                "2 SURN Stone",
                "2 NSFX Jr.",
            ],
            "names.ged",
            0,
        )
        person = gm._individual_from_record(record)
        assert person.names == (
            gm.PersonalName(
                value="Dr. Ana /Stone/ Jr.",
                given="Ana",
                surname="Stone",
                prefix="Dr.",
                suffix="Jr.",
                nickname="Annie",
                name_type="married",
                is_primary=True,
            ),
        )

    def test_merging_preserves_names_and_source_provenance(self) -> None:
        first = gm.IndividualRecord(
            "@I1@",
            names=(gm.PersonalName("Ana /Reed/", is_primary=True),),
            source_file="a.ged",
            source_files=("a.ged",),
        )
        second = gm.IndividualRecord(
            "@I2@",
            names=(gm.PersonalName("Ana /Stone/", name_type="married"),),
            source_file="b.ged",
            source_files=("b.ged",),
        )
        merged = gm.merge_two_records(first, second)
        assert len(merged.names) == 2
        assert merged.source_files == ("a.ged", "b.ged")

    def test_fallback_serialization_retains_components(self) -> None:
        person = gm.IndividualRecord(
            "@I1@",
            names=(
                gm.PersonalName(
                    "Ana /Stone/",
                    given="Ana",
                    surname="Stone",
                    nickname="Annie",
                    name_type="married",
                    is_primary=True,
                ),
            ),
        )
        serialized = gm._record_to_gedcom_lines(person)
        assert "1 NAME Ana /Stone/" in serialized
        assert "2 TYPE married" in serialized
        assert "2 NICK Annie" in serialized


class TestDuplicateAndMarriedNameAnalysis:
    """Exercise high-confidence duplicate and conservative surname rules."""

    def test_reports_same_source_pair_at_threshold(self) -> None:
        duplicates = [
            finding
            for finding in _fixture_report().findings
            if finding.code == "POSSIBLE_DUPLICATE"
        ]
        assert any(
            set(finding.person_pointers) == {"@A_DUP_ONE@", "@A_DUP_TWO@"} for finding in duplicates
        )

    def test_name_only_pair_remains_below_report_threshold(self) -> None:
        first = gm.IndividualRecord("@I1@", given_name="Nell", surname="Ember")
        second = gm.IndividualRecord("@I2@", given_name="Nell", surname="Ember")
        assert gm.assess_similarity(first, second).score == 88.0
        assert gm._quality_duplicate_pairs([first, second]) == []

    def test_typed_married_primary_is_high_severity(self) -> None:
        findings = [
            finding
            for finding in _fixture_report().findings
            if finding.code == "POSSIBLE_MARRIED_PRIMARY_NAME"
        ]
        bad = next(finding for finding in findings if finding.person_pointers == ("@A_NAME_BAD@",))
        assert bad.severity == "high"

    def test_birth_name_primary_is_not_flagged(self) -> None:
        flagged = {
            pointer
            for finding in _fixture_report().findings
            if finding.code == "POSSIBLE_MARRIED_PRIMARY_NAME"
            for pointer in finding.person_pointers
        }
        assert "@A_NAME_GOOD@" not in flagged
        assert "@B_NAME_NEGATIVE@" not in flagged


class TestAncestryAndDataQuality:
    """Verify iterative ancestry, chronology, diagnostics, and report ranking."""

    def test_generations_and_pedi_are_reported(self) -> None:
        report = _fixture_report()
        assert ("@A_ROOT@", 0, "self") in report.ancestor_relationships
        assert ("@A_FATHER@", 1, "birth/unspecified") in report.ancestor_relationships

    def test_iterative_cycle_detection_terminates(self) -> None:
        records = [
            gm.GedcomRecord(["0 @F1@ FAM", "1 HUSB @I2@", "1 CHIL @I1@"], "x.ged", 0),
            gm.GedcomRecord(["0 @F2@ FAM", "1 HUSB @I1@", "1 CHIL @I2@"], "x.ged", 1),
        ]
        generations, cycles = gm.ancestor_generations("@I1@", records)
        assert generations == {"@I1@": 0, "@I2@": 1}
        assert cycles == {"@I1@", "@I2@"}

    def test_adopted_parentage_is_labeled_for_ancestor(self) -> None:
        root_record = gm.GedcomRecord(
            [
                "0 @I1@ INDI",
                "1 NAME Root /Person/",
                "1 FAMC @F1@",
                "2 PEDI adopted",
            ],
            "adoption.ged",
            0,
        )
        parent_record = gm.GedcomRecord(
            ["0 @I2@ INDI", "1 NAME Parent /Person/", "1 FAMS @F1@"],
            "adoption.ged",
            1,
        )
        family_record = gm.GedcomRecord(
            ["0 @F1@ FAM", "1 HUSB @I2@", "1 CHIL @I1@"],
            "adoption.ged",
            2,
        )
        records = [root_record, parent_record, family_record]
        people = gm.enrich_relationship_context(
            [
                gm._individual_from_record(root_record),
                gm._individual_from_record(parent_record),
            ],
            records,
        )
        source = gm.ParsedSource(Path("adoption.ged"), records, {})
        report = gm.analyze_quality(people, records, [source], "@I1@")
        assert ("@I2@", 1, "adopted") in report.ancestor_relationships

    def test_fixture_reports_invalid_and_alternative_dates(self) -> None:
        codes = {finding.code for finding in _fixture_report().findings}
        assert "INVALID_DATE" in codes
        assert "ALTERNATIVE_VITAL_EVENTS" in codes

    def test_missing_death_requires_age_120(self) -> None:
        report = _fixture_report()
        death_targets = {
            pointer
            for finding in report.findings
            if finding.code == "MISSING_DEATH_DATE"
            for pointer in finding.person_pointers
        }
        assert "@A_FATHER@" in death_targets
        assert "@A_COUSIN@" not in death_targets

    def test_structural_and_reciprocity_findings_exist(self) -> None:
        codes = {finding.code for finding in _fixture_report().findings}
        assert {"MISSING_TRLR", "DUPLICATE_TRLR", "MISSING_CHARSET"} <= codes
        assert "DANGLING_REFERENCE" in codes
        assert "NONRECIPROCAL_FAMILY_REFERENCE" in codes

    def test_finding_ids_and_order_are_stable(self) -> None:
        first = _fixture_report()
        second = _fixture_report()
        assert [finding.finding_id for finding in first.findings] == [
            finding.finding_id for finding in second.findings
        ]
        assert first.findings[0].severity in {"critical", "high"}


class TestGedcomServiceRootErrors:
    """Keep root-resolution failures coded and free of genealogy values."""

    @pytest.mark.parametrize("operation", ("merge", "subtree", "quality"))
    def test_unresolved_root_is_a_sanitized_service_error(
        self,
        tmp_path: Path,
        operation: str,
    ) -> None:
        requested = "PRIVATE-PAYLOAD fictional root person"
        output = tmp_path / f"{operation}.out"
        output.write_bytes(b"sentinel\n")
        service = GedcomService()

        with pytest.raises(AncestryError) as raised:
            if operation == "merge":
                service.merge(
                    [PUBLIC_SOURCE_A, PUBLIC_SOURCE_B],
                    output,
                    root_person=requested,
                )
            elif operation == "subtree":
                service.subtree(
                    PUBLIC_SOURCE_A,
                    output,
                    root_person=requested,
                )
            else:
                service.quality(
                    PUBLIC_SOURCE_A,
                    output,
                    root_person=requested,
                )

        error = raised.value
        assert error.code == "GEDCOM_ROOT_PERSON_UNRESOLVED"
        assert error.exit_code == 2
        assert error.details == {}
        assert error.__context__ is None
        assert requested not in error.render()
        assert requested not in repr(error)
        assert output.read_bytes() == b"sentinel\n"


class TestMarkdownAndCli:
    """Verify atomic output, root semantics, defaults, and rejected syntax."""

    def test_markdown_contains_all_required_sections_and_escaping(self) -> None:
        finding = dataclasses.replace(_fixture_report().findings[0], recommendation="Compare A | B")
        report = dataclasses.replace(_fixture_report(), findings=(finding,))
        rendered = gm.render_quality_report(report)
        for heading in (
            "Top 25 actions",
            "Direct ancestors by generation",
            "High-confidence possible duplicates",
            "Possible married-name-as-primary issues",
            "General tree quality",
            "Merge decisions",
            "Source and structural diagnostics",
        ):
            assert f"## {heading}" in rendered
        assert "Compare A \\| B" in rendered

    def test_atomic_quality_write_replaces_destination(self, tmp_path: Path) -> None:
        destination = tmp_path / "quality.md"
        destination.write_text("old", encoding="utf-8")
        gm.write_quality_report(_fixture_report(), destination)
        assert destination.read_text(encoding="utf-8").startswith("# GEDCOM Merge Quality Report")

    def test_default_report_and_quality_root_do_not_filter_export(self, tmp_path: Path) -> None:
        output = tmp_path / "master.ged"
        result = gm.main(
            [
                str(SOURCE_A),
                str(SOURCE_B),
                "--ai-backend",
                "none",
                "--auto",
                "--quality-root-person",
                "Maren Hollow",
                "-o",
                str(output),
            ]
        )
        assert result == 0
        assert output.with_suffix(".quality.md").is_file()
        assert output.read_text(encoding="utf-8").count(" INDI") == 23

    def test_report_can_be_disabled_without_a_root(self, tmp_path: Path) -> None:
        output = tmp_path / "master.ged"
        result = gm.main(
            [
                str(SOURCE_A),
                str(SOURCE_B),
                "--ai-backend",
                "none",
                "--auto",
                "--no-quality-report",
                "-o",
                str(output),
            ]
        )
        assert result == 0
        assert output.is_file()
        assert not output.with_suffix(".quality.md").exists()

    def test_fixture_merge_preserves_people_edges_names_and_custom_facts(
        self, tmp_path: Path
    ) -> None:
        output = tmp_path / "fidelity.ged"
        result = gm.main(
            [
                str(SOURCE_A),
                str(SOURCE_B),
                "--ai-backend",
                "none",
                "--auto",
                "--quality-root-person",
                "Maren Hollow",
                "-o",
                str(output),
            ]
        )
        text = output.read_text(encoding="utf-8")
        assert result == 0
        assert "1 NAME Cato /Hollow/" in text
        assert "1 NAME Maren /Vale/" in text
        assert "2 PEDI adopted" in text
        assert "1 _PROFILE Rich duplicate" in text
        assert "2 DATE 02 FEB 1960" in text
        assert "2 DATE 02 FEB 1964" in text
        assert "1 CHIL @A_COUSIN@" in text

    def test_successful_report_requires_root(self, tmp_path: Path) -> None:
        output = tmp_path / "master.ged"
        result = gm.main(
            [
                str(SOURCE_A),
                str(SOURCE_B),
                "--ai-backend",
                "none",
                "--auto",
                "-o",
                str(output),
            ]
        )
        assert result == 1
        assert not output.exists()

    def test_malformed_input_writes_only_diagnostic_without_root(self, tmp_path: Path) -> None:
        output = tmp_path / "rejected.ged"
        result = gm.main(
            [
                str(SOURCE_A),
                str(MALFORMED),
                "--ai-backend",
                "none",
                "--auto",
                "-o",
                str(output),
            ]
        )
        diagnostic = output.with_suffix(".quality.md")
        assert result == 1
        assert not output.exists()
        assert "Line: 8" in diagnostic.read_text(encoding="utf-8")
        assert str(MALFORMED.resolve()) in diagnostic.read_text(encoding="utf-8")


class TestQualityAiRefinement:
    """Exercise the provider-neutral advisory resolver."""

    def test_provider_refinement_is_bounded_and_advisory(self) -> None:
        report = _fixture_report()
        finding = report.findings[0]
        resolution = QualityResolution(
            annotations={finding.finding_id: ("Useful context", ("Check records",))},
            provider_id="ollama",
            model="mock-model",
            remote=False,
        )

        refined = gm.refine_quality_report_with_ai(report, lambda _report: resolution)

        assert refined.findings[0].severity == finding.severity
        assert refined.findings[0].evidence == finding.evidence
        assert refined.findings[0].ai_why == "Useful context"
        assert refined.ai_refined is True

    def test_provider_failure_propagates_as_stable_error(self) -> None:
        report = _fixture_report()

        def fail(_report: gm.QualityReport) -> QualityResolution:
            raise ProviderError("PROVIDER_TIMEOUT", "The provider request timed out.")

        with pytest.raises(ProviderError) as raised:
            gm.refine_quality_report_with_ai(report, fail)
        assert raised.value.code == "PROVIDER_TIMEOUT"

    def test_unknown_model_finding_ids_are_ignored(self) -> None:
        parsed = gm._quality_annotations_from_payload(
            {
                "annotations": [
                    {
                        "finding_id": "invented",
                        "why_this_matters": "x",
                        "research_suggestions": [],
                    }
                ]
            },
            {"known"},
        )
        assert parsed == {}


class TestDocumentationContract:
    """Keep executable examples, fixtures, and documented flags synchronized."""

    def test_quality_and_provider_flags_are_documented(self) -> None:
        repository = Path(__file__).parents[1]
        compatibility = (repository / "docs" / "GEDCOM_COMPATIBILITY.md").read_text(
            encoding="utf-8"
        )
        cli = (repository / "docs" / "CLI.md").read_text(encoding="utf-8")
        for flag in ("--quality-report", "--root-person", "--provider"):
            assert flag in compatibility
        for flag in ("--provider", "--model", "--consent"):
            assert flag in cli

    def test_public_demo_fixture_names_and_root_are_synchronized(self) -> None:
        repository = Path(__file__).parents[1]
        texts = [
            (FIXTURES / "README.md").read_text(encoding="utf-8"),
            (repository / "docs" / "GEDCOM_COMPATIBILITY.md").read_text(encoding="utf-8"),
            (repository / "scripts" / "gedcom_merge_quickstart.sh").read_text(encoding="utf-8"),
        ]
        assert all("xref-source-a.ged" in text for text in texts)
        assert all("xref-source-b.ged" in text for text in texts)
        assert all("Aster Fiction" in text for text in texts)

    def test_quickstart_shell_has_valid_syntax(self) -> None:
        script = Path(__file__).parents[1] / "scripts" / "gedcom_merge_quickstart.sh"
        subprocess.run(["bash", "-n", str(script)], check=True)

    def test_documented_offline_demo_smoke(self, tmp_path: Path) -> None:
        script = Path(__file__).parents[1] / "scripts" / "gedcom_merge_quickstart.sh"
        caller_config = tmp_path / "caller-config"
        caller_data = tmp_path / "caller-data"
        result = subprocess.run(
            [str(script), "--skip-install", "--output-dir", str(tmp_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={
                **os.environ,
                "ANCESTRYLLM_PYTHON": sys.executable,
                "ANCESTRYLLM_CONFIG_DIR": str(caller_config),
                "ANCESTRYLLM_DATA_DIR": str(caller_data),
                "XDG_CONFIG_HOME": str(tmp_path / "caller-xdg-config"),
                "XDG_DATA_HOME": str(tmp_path / "caller-xdg-data"),
            },
        )
        assert result.returncode == 0, result.stderr
        assert "GEDCOM merge demo passed" in result.stdout
        assert not caller_config.exists()
        assert not caller_data.exists()
        assert not (tmp_path / "caller-xdg-config").exists()
        assert not (tmp_path / "caller-xdg-data").exists()
        runs = list(tmp_path.glob("gedcom-merge-*"))
        assert len(runs) == 1
        assert (runs[0] / ".ancestryllm" / "config").is_dir()
        assert (runs[0] / ".ancestryllm" / "data").is_dir()
