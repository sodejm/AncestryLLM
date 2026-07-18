"""Tests for tools.gedcom_merge.

Fixtures are constructed programmatically or written to pytest temporary
directories, so the suite runs without the network, Ollama, or remote APIs.
"""

from __future__ import annotations

import dataclasses
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import tools.gedcom_merge as gm


# ---------------------------------------------------------------------------
# Date normalisation
# ---------------------------------------------------------------------------


class TestNormaliseGedcomDate:
    """Verify that a wide range of input formats are normalised correctly."""

    def test_iso_date_with_dashes(self):
        assert gm.normalise_gedcom_date("1850-07-15") == "15 JUL 1850"

    def test_iso_date_with_slashes(self):
        assert gm.normalise_gedcom_date("1850/07/15") == "15 JUL 1850"

    def test_already_valid_gedcom_full_date(self):
        assert gm.normalise_gedcom_date("15 JUL 1850") == "15 JUL 1850"

    def test_year_only(self):
        assert gm.normalise_gedcom_date("1850") == "1850"

    def test_approximate_qualifier_abt(self):
        result = gm.normalise_gedcom_date("abt 1900")
        assert result == "ABT 1900"

    def test_approximate_qualifier_circa(self):
        result = gm.normalise_gedcom_date("circa 1850")
        assert result == "ABT 1850"

    def test_before_qualifier(self):
        assert gm.normalise_gedcom_date("bef 1900") == "BEF 1900"

    def test_after_qualifier(self):
        assert gm.normalise_gedcom_date("aft 1900") == "AFT 1900"

    def test_estimated_qualifier(self):
        assert gm.normalise_gedcom_date("est 1900") == "EST 1900"

    def test_gedcom_qualifier_passthrough(self):
        assert gm.normalise_gedcom_date("ABT 1900") == "ABT 1900"

    def test_range_passthrough(self):
        date = "BET 01 JAN 1840 AND 31 DEC 1845"
        assert gm.normalise_gedcom_date(date) == date

    def test_empty_string_returns_empty(self):
        assert gm.normalise_gedcom_date("") == ""

    def test_none_type_like_empty(self):
        # The function accepts empty-ish strings without raising.
        assert gm.normalise_gedcom_date("   ") == "   "

    def test_unparseable_string_returns_unchanged(self):
        bad = "not a date at all XYZ"
        # Should not raise; returns the original string.
        result = gm.normalise_gedcom_date(bad)
        assert isinstance(result, str)

    def test_month_year_only(self):
        assert gm.normalise_gedcom_date("JUL 1850") == "JUL 1850"

    def test_natural_language_date(self):
        # dateutil can parse "July 15, 1850".
        result = gm.normalise_gedcom_date("July 15, 1850")
        assert result == "15 JUL 1850"

    def test_leading_zeroes_on_day(self):
        # Day should be zero-padded to two digits.
        assert gm.normalise_gedcom_date("1850-01-05") == "05 JAN 1850"


# ---------------------------------------------------------------------------
# IndividualRecord helpers
# ---------------------------------------------------------------------------


def _make_record(**kwargs) -> gm.IndividualRecord:
    """Construct an IndividualRecord with sensible defaults for tests."""
    defaults = dict(
        pointer="@I1@",
        given_name="John",
        surname="Smith",
        birth_date="15 JUL 1850",
        death_date="01 JAN 1920",
        gender="M",
        source_file="/fake/file_a.ged",
    )
    defaults.update(kwargs)
    return gm.IndividualRecord(**defaults)


class TestIndividualRecord:
    def test_full_name(self):
        rec = _make_record(given_name="John", surname="Smith")
        assert rec.full_name == "John Smith"

    def test_full_name_no_surname(self):
        rec = _make_record(given_name="John", surname="")
        assert rec.full_name == "John"

    def test_birth_year_extracted(self):
        rec = _make_record(birth_date="15 JUL 1850")
        assert rec.birth_year == 1850

    def test_death_year_extracted(self):
        rec = _make_record(death_date="ABT 1920")
        assert rec.death_year == 1920

    def test_birth_year_none_when_no_date(self):
        rec = _make_record(birth_date="")
        assert rec.birth_year is None

    def test_summary_contains_name(self):
        rec = _make_record(given_name="Jane", surname="Doe")
        assert "Jane Doe" in rec.summary()

    def test_summary_contains_birth_date(self):
        rec = _make_record(birth_date="1850")
        assert "1850" in rec.summary()


# ---------------------------------------------------------------------------
# Structured GEDCOM identity evidence
# ---------------------------------------------------------------------------


def _parse_individual(lines, source="/source.ged"):
    """Parse one synthetic INDI block through the production extractor."""
    return gm._individual_from_record(gm.GedcomRecord(lines, source, 0))


class TestIdentityFactExtraction:
    """Cover five high-risk fact-extraction and preservation edge cases."""

    def test_country_aliases_are_normalized(self):
        person = _parse_individual([
            "0 @I1@ INDI",
            "1 NAME Jane /Doe/",
            "1 BIRT",
            "2 PLAC Boston, Massachusetts, USA",
            "1 DEAT",
            "2 PLAC London, England",
        ])
        assert person.birth_country == "united states"
        assert person.death_country == "united kingdom"

    def test_multiple_birth_facts_are_retained(self):
        person = _parse_individual([
            "0 @I1@ INDI",
            "1 NAME Jane /Doe/",
            "1 BIRT",
            "2 DATE 1900",
            "1 BIRT",
            "2 DATE 01 JAN 1900",
            "2 PLAC Boston, Massachusetts, USA",
        ])
        assert len(person.facts["BIRT"]) == 2
        assert person.birth_date == "01 JAN 1900"
        assert "Boston" in person.birth_place

    def test_occupation_and_residence_are_structured(self):
        person = _parse_individual([
            "0 @I1@ INDI",
            "1 NAME Jane /Doe/",
            "1 OCCU Textile worker",
            "2 DATE 1920",
            "2 PLAC Lowell, Massachusetts, USA",
            "1 RESI",
            "2 DATE 1930",
            "2 ADDR 10 Main Street",
            "3 CITY Boston",
            "3 STAE Massachusetts",
            "3 CTRY United States",
        ])
        assert person.occupations[0].value == "Textile worker"
        assert person.occupations[0].date == "1920"
        assert person.residences[0].effective_country == "united states"
        assert "Boston" in person.residences[0].place

    def test_alternate_names_are_not_overwritten(self):
        person = _parse_individual([
            "0 @I1@ INDI",
            "1 NAME William /Smith/",
            "1 NAME Bill /Smith/",
            "1 NAME Wilhelm /Schmidt/",
        ])
        assert person.full_name == "William Smith"
        assert person.alternate_names == ("Bill Smith", "Wilhelm Schmidt")

    def test_standard_and_custom_facts_have_safe_roles(self):
        person = _parse_individual([
            "0 @I1@ INDI",
            "1 NAME Jane /Doe/",
            "1 CENS",
            "2 DATE 01 APR 1940",
            "2 PLAC Boston, Massachusetts, USA",
            "1 _VENDOR secret vendor value",
        ])
        assert person.facts["CENS"][0].date == "01 APR 1940"
        assert "_VENDOR" not in person.facts
        assert "_VENDOR" in person.extra_fields


# ---------------------------------------------------------------------------
# Relationship enrichment
# ---------------------------------------------------------------------------


def _family_record(pointer, lines, sequence=0):
    """Build one synthetic FAM record for relationship tests."""
    return gm.GedcomRecord([f"0 {pointer} FAM", *lines], "/family.ged", sequence)


class TestRelationshipEnrichment:
    """Cover five partner, marriage, dependent, and pedigree edge cases."""

    def test_partner_and_marriage_are_resolved(self):
        people = [
            _make_record(pointer="@I1@", given_name="Jane", surname="Doe"),
            _make_record(pointer="@I2@", given_name="Alex", surname="Roe"),
        ]
        family = _family_record("@F1@", [
            "1 WIFE @I1@",
            "1 HUSB @I2@",
            "1 MARR",
            "2 DATE 15 JUN 1920",
            "2 PLAC Boston, Massachusetts, USA",
        ])
        enriched = gm.enrich_relationship_context(people, [family])
        assert enriched[0].partner_names == ("Alex Roe",)
        assert enriched[0].marriages[0].date == "15 JUN 1920"
        assert enriched[0].marriages[0].effective_country == "united states"

    def test_parent_child_edges_are_direction_aware(self):
        parents = [
            _make_record(pointer="@I1@", given_name="Jane", surname="Doe"),
            _make_record(pointer="@I2@", given_name="Alex", surname="Doe"),
        ]
        child = _make_record(
            pointer="@I3@",
            given_name="Casey",
            surname="Doe",
        )
        family = _family_record("@F1@", [
            "1 WIFE @I1@",
            "1 HUSB @I2@",
            "1 CHIL @I3@",
        ])
        enriched = gm.enrich_relationship_context([*parents, child], [family])
        assert {relative.name for relative in enriched[0].children} == {"Casey Doe"}
        assert {relative.name for relative in enriched[2].parents} == {
            "Jane Doe",
            "Alex Doe",
        }

    def test_child_details_support_sparse_parent(self):
        parent = _make_record(
            pointer="@I1@",
            given_name="Jane",
            surname="Doe",
            birth_date="",
            death_date="",
        )
        child = _make_record(
            pointer="@I2@",
            given_name="Casey",
            surname="Doe",
            birth_date="01 JAN 1940",
        )
        family = _family_record("@F1@", ["1 WIFE @I1@", "1 CHIL @I2@"])
        enriched = gm.enrich_relationship_context([parent, child], [family])
        assert enriched[0].children[0].birth_date == "01 JAN 1940"

    def test_pedigree_relationship_is_preserved(self):
        parent = _make_record(
            pointer="@I1@",
            given_name="Jane",
            surname="Doe",
        )
        child = _make_record(
            pointer="@I2@",
            given_name="Casey",
            surname="Doe",
            raw_lines=[
                "0 @I2@ INDI",
                "1 NAME Casey /Doe/",
                "1 FAMC @F1@",
                "2 PEDI adopted",
            ],
        )
        family = _family_record("@F1@", ["1 WIFE @I1@", "1 CHIL @I2@"])
        enriched = gm.enrich_relationship_context([parent, child], [family])
        assert enriched[0].children[0].relationship == "adopted"
        assert enriched[1].parents[0].relationship == "adopted"

    def test_multiple_unions_and_unknown_pointers_are_safe(self):
        person = _make_record(pointer="@I1@", given_name="Jane", surname="Doe")
        first = _family_record("@F1@", [
            "1 WIFE @I1@",
            "1 HUSB @MISSING@",
            "1 MARR",
            "2 DATE 1920",
        ])
        second = _family_record("@F2@", [
            "1 WIFE @I1@",
            "1 MARR",
            "2 DATE 1930",
        ])
        enriched = gm.enrich_relationship_context([person], [first, second])
        assert enriched[0].partners == ()
        assert {fact.date for fact in enriched[0].marriages} == {"1920", "1930"}


# ---------------------------------------------------------------------------
# Similarity scoring
# ---------------------------------------------------------------------------


class TestSimilarityScore:
    def test_identical_records_score_100(self):
        a = _make_record(pointer="@I1@", source_file="/a.ged")
        b = _make_record(pointer="@I2@", source_file="/b.ged")
        assert gm.similarity_score(a, b) == 100.0

    def test_different_names_lower_score(self):
        a = _make_record(given_name="John", surname="Smith", source_file="/a.ged")
        b = _make_record(given_name="Mary", surname="Jones", source_file="/b.ged")
        score = gm.similarity_score(a, b)
        assert score <= 70

    def test_same_name_different_years_lower_score(self):
        a = _make_record(birth_date="1850", death_date="1920", source_file="/a.ged")
        b = _make_record(birth_date="1780", death_date="1850", source_file="/b.ged")
        score = gm.similarity_score(a, b)
        # Exact names cannot overcome two strong life-year contradictions.
        assert score < 80

    def test_gender_mismatch_reduces_score(self):
        a = _make_record(gender="M", source_file="/a.ged")
        b = _make_record(gender="F", source_file="/b.ged")
        score_mismatch = gm.similarity_score(a, b)
        a2 = _make_record(gender="M", source_file="/a.ged")
        b2 = _make_record(gender="M", source_file="/b.ged")
        score_match = gm.similarity_score(a2, b2)
        assert score_mismatch < score_match

    def test_missing_names_still_returns_score(self):
        a = _make_record(given_name="", surname="", source_file="/a.ged")
        b = _make_record(given_name="", surname="", source_file="/b.ged")
        score = gm.similarity_score(a, b)
        assert 0 <= score <= 100


class TestEvidenceAwareSimilarity:
    """Cover five false-merge and sparse-relative scoring edge cases."""

    def test_name_only_cannot_auto_merge(self):
        a = _make_record(
            birth_date="",
            death_date="",
            gender="",
            source_file="/a.ged",
        )
        b = _make_record(
            pointer="@I2@",
            birth_date="",
            death_date="",
            gender="",
            source_file="/b.ged",
        )
        assessment = gm.assess_similarity(a, b)
        assert assessment.score == 88.0
        assert assessment.automatic_merge_safe is False

    def test_sparse_parent_can_match_through_partner_and_child(self):
        partner_a = gm.RelativeIdentity("@P1@", "Alex Doe", "1900")
        partner_b = gm.RelativeIdentity("@P9@", "Alex Doe", "1900")
        child_a = gm.RelativeIdentity("@C1@", "Casey Doe", "1940")
        child_b = gm.RelativeIdentity("@C9@", "Casey Doe", "1940")
        residence = gm.GenealogicalFact(
            "RESI",
            date="1930",
            place="Boston, Massachusetts, USA",
        )
        a = _make_record(
            birth_date="",
            death_date="",
            gender="",
            partners=(partner_a,),
            children=(child_a,),
            facts={"RESI": (residence,)},
            source_file="/a.ged",
        )
        b = _make_record(
            pointer="@I2@",
            birth_date="",
            death_date="",
            gender="",
            partners=(partner_b,),
            children=(child_b,),
            facts={"RESI": (residence,)},
            source_file="/b.ged",
        )
        assessment = gm.assess_similarity(a, b)
        assert assessment.score == 100.0
        assert assessment.automatic_merge_safe is True
        family_only = gm.assess_similarity(
            dataclasses.replace(a, facts={}),
            dataclasses.replace(b, facts={}),
        )
        assert family_only.automatic_merge_safe is False

    def test_conflicting_birth_countries_block_auto_merge(self):
        a = _make_record(
            birth_place="Boston, Massachusetts, USA",
            source_file="/a.ged",
        )
        b = _make_record(
            pointer="@I2@",
            birth_place="Toronto, Ontario, Canada",
            source_file="/b.ged",
        )
        assessment = gm.assess_similarity(a, b)
        assert "birth country" in assessment.conflicts
        assert assessment.automatic_merge_safe is False

    def test_matching_work_and_residence_add_evidence(self):
        occupation = gm.GenealogicalFact("OCCU", "Carpenter", "1920")
        shared_residence = gm.GenealogicalFact(
            "RESI",
            date="1930",
            place="Boston, Massachusetts, USA",
        )
        extra_residence = gm.GenealogicalFact(
            "RESI",
            date="1940",
            place="Cambridge, Massachusetts, USA",
        )
        baseline_a = _make_record(birth_date="", death_date="", gender="")
        baseline_b = _make_record(
            pointer="@I2@",
            birth_date="",
            death_date="",
            gender="",
        )
        rich_a = dataclasses.replace(
            baseline_a,
            facts={"OCCU": (occupation,), "RESI": (shared_residence,)},
        )
        rich_b = dataclasses.replace(
            baseline_b,
            facts={
                "OCCU": (occupation,),
                "RESI": (shared_residence, extra_residence),
            },
        )
        assert gm.similarity_score(rich_a, rich_b) > gm.similarity_score(
            baseline_a,
            baseline_b,
        )

    def test_disjoint_well_populated_children_are_a_conflict(self):
        left_children = (
            gm.RelativeIdentity("@C1@", "Alpha One"),
            gm.RelativeIdentity("@C2@", "Beta Two"),
        )
        right_children = (
            gm.RelativeIdentity("@C8@", "Xylophone Zed"),
            gm.RelativeIdentity("@C9@", "Quasar Voss"),
        )
        a = _make_record(children=left_children, source_file="/a.ged")
        b = _make_record(
            pointer="@I2@",
            children=right_children,
            source_file="/b.ged",
        )
        assessment = gm.assess_similarity(a, b)
        assert "children" in assessment.conflicts
        assert assessment.score <= 84.0
        assert assessment.automatic_merge_safe is False


class TestIdentitySafetyRegressions:
    """Cover failures that could collapse distinct people or family events."""

    def test_state_or_province_is_not_inferred_as_country(self):
        assert gm._country_from_place("Boston, Massachusetts") == ""
        assert gm._country_from_place("Toronto, Ontario") == ""
        assert gm._country_from_place("Boston, Massachusetts, USA") == (
            "united states"
        )

    def test_different_family_event_tags_do_not_match(self):
        marriage = gm.GenealogicalFact("MARR", date="1920", place="Boston")
        divorce = gm.GenealogicalFact("DIV", date="1920", place="Boston")
        assert gm._fact_similarity(marriage, divorce) == 0.0

    def test_conflicting_alternative_countries_force_review(self):
        shared = gm.GenealogicalFact(
            "BIRT",
            date="1900",
            place="Boston, Massachusetts, USA",
        )
        conflict = gm.GenealogicalFact(
            "BIRT",
            date="1900",
            place="Toronto, Ontario, Canada",
        )
        a = _make_record(facts={"BIRT": (shared, conflict,)})
        b = _make_record(pointer="@I2@", facts={"BIRT": (shared,)})
        assessment = gm.assess_similarity(a, b)
        assert "birth country alternatives" in assessment.conflicts
        assert assessment.automatic_merge_safe is False

    def test_one_relative_cannot_match_two_children(self):
        left = (
            gm.RelativeIdentity("@C1@", "Alex Smith", "1940"),
            gm.RelativeIdentity("@C2@", "Alex Smith", "1940"),
        )
        right = (
            gm.RelativeIdentity("@C8@", "Alex Smith", "1940"),
            gm.RelativeIdentity("@C9@", "Quasar Jones", "1975"),
        )
        score = gm._collection_similarity(left, right, gm._relative_similarity)
        assert score < 70.0

    def test_transitive_cluster_conflict_retains_third_person(self):
        usa = gm.GenealogicalFact(
            "BIRT",
            date="1900",
            place="Boston, Massachusetts, USA",
        )
        canada = gm.GenealogicalFact(
            "BIRT",
            date="1900",
            place="Toronto, Ontario, Canada",
        )
        a = _make_record(pointer="@A@", facts={"BIRT": (usa,)})
        bridge = _make_record(
            pointer="@B@",
            birth_place="",
            facts={},
            source_file="/b.ged",
        )
        c = _make_record(
            pointer="@C@",
            facts={"BIRT": (canada,)},
            source_file="/c.ged",
        )
        verdict = {
            "is_duplicate": True,
            "confidence": 1.0,
            "reasoning": "test bridge",
            "preferred_values": {},
        }
        candidates = [(0, 1, 100.0), (1, 2, 95.0)]
        with (
            patch(
                "tools.gedcom_merge.find_duplicate_candidates",
                return_value=candidates,
            ),
            patch("tools.gedcom_merge.ai_resolve", return_value=verdict),
        ):
            merged = gm.merge_records([a, bridge, c], auto=True)
        assert {person.pointer for person in merged} == {"@A@", "@C@"}


class TestAiPromptContext:
    """Verify useful structured evidence is sent without free-form source data."""

    def test_prompt_contains_extended_identity_context(self):
        person = _make_record(
            birth_place="Boston, Massachusetts, USA",
            facts={
                "OCCU": (gm.GenealogicalFact("OCCU", "Carpenter"),),
                "RESI": (gm.GenealogicalFact("RESI", date="1930", place="Boston"),),
            },
            marriages=(gm.GenealogicalFact("MARR", date="1920"),),
            partners=(gm.RelativeIdentity("@P1@", "Alex Smith"),),
            children=(gm.RelativeIdentity("@C1@", "Casey Smith", "1940"),),
        )
        prompt = gm._build_dedup_prompt(person, person)
        for expected in (
            "b.country=united states",
            "Carpenter",
            "residences",
            "marriages",
            "Alex Smith",
            "Casey Smith",
        ):
            assert expected in prompt

    def test_prompt_excludes_notes_sources_and_vendor_text(self):
        person = _make_record(
            extra_fields={
                "NOTE": ["1 NOTE private medical detail\n"],
                "SOUR": ["1 SOUR secret source text\n"],
                "_VENDOR": ["1 _VENDOR private extension\n"],
            },
        )
        prompt = gm._build_dedup_prompt(person, person)
        assert "private medical detail" not in prompt
        assert "secret source text" not in prompt
        assert "private extension" not in prompt


# ---------------------------------------------------------------------------
# Duplicate candidate detection
# ---------------------------------------------------------------------------


class TestFindDuplicateCandidates:
    def test_finds_high_confidence_pair(self):
        a = _make_record(pointer="@I1@", source_file="/a.ged")
        b = _make_record(pointer="@I2@", source_file="/b.ged")
        candidates = gm.find_duplicate_candidates([a, b], threshold=80)
        assert len(candidates) == 1
        idx_a, idx_b, score = candidates[0]
        assert score >= 80

    def test_same_source_file_not_a_candidate(self):
        a = _make_record(pointer="@I1@", source_file="/same.ged")
        b = _make_record(pointer="@I2@", source_file="/same.ged")
        candidates = gm.find_duplicate_candidates([a, b], threshold=80)
        assert candidates == []

    def test_dissimilar_pair_not_returned(self):
        a = _make_record(
            given_name="John",
            surname="Smith",
            birth_date="1850",
            source_file="/a.ged",
        )
        b = _make_record(
            given_name="Mary",
            surname="Jones",
            birth_date="1790",
            source_file="/b.ged",
        )
        candidates = gm.find_duplicate_candidates([a, b], threshold=80)
        assert candidates == []

    def test_candidates_sorted_descending_by_score(self):
        # Three individuals: A is a near-perfect match for B but only a weak
        # match for C.
        a = _make_record(pointer="@I1@", source_file="/a.ged")
        b = _make_record(pointer="@I2@", source_file="/b.ged")
        c = _make_record(
            pointer="@I3@",
            given_name="Mary",
            surname="Jones",
            birth_date="1780",
            source_file="/b.ged",
        )
        candidates = gm.find_duplicate_candidates([a, b, c], threshold=0)
        scores = [s for _, _, s in candidates]
        assert scores == sorted(scores, reverse=True)

    def test_alternate_name_can_create_candidate_block(self):
        a = _make_record(
            pointer="@I1@",
            given_name="William",
            surname="Smith",
            source_file="/a.ged",
        )
        b = _make_record(
            pointer="@I2@",
            given_name="Bill",
            surname="Jones",
            alternate_names=("William Smith",),
            source_file="/b.ged",
        )
        candidates = gm.find_duplicate_candidates([a, b], threshold=70)
        assert len(candidates) == 1


# ---------------------------------------------------------------------------
# Merging two records
# ---------------------------------------------------------------------------


class TestMergeTwoRecords:
    def test_empty_fields_filled_from_secondary(self):
        primary = _make_record(pointer="@I1@", birth_place="", death_place="")
        secondary = _make_record(
            pointer="@I2@",
            birth_place="London, England",
            death_place="New York, USA",
        )
        merged = gm.merge_two_records(primary, secondary)
        assert merged.birth_place == "London, England"
        assert merged.death_place == "New York, USA"

    def test_primary_pointer_preserved(self):
        primary = _make_record(pointer="@I1@")
        secondary = _make_record(pointer="@I2@")
        merged = gm.merge_two_records(primary, secondary)
        assert merged.pointer == "@I1@"

    def test_more_specific_date_wins(self):
        primary = _make_record(birth_date="1850")
        secondary = _make_record(birth_date="15 JUL 1850")
        merged = gm.merge_two_records(primary, secondary)
        assert merged.birth_date == "15 JUL 1850"

    def test_extra_fields_combined(self):
        primary = _make_record(extra_fields={"OCCU": ["1 OCCU Farmer\n"]})
        secondary = _make_record(extra_fields={
            "OCCU": ["1 OCCU Farmer\n", "1 OCCU Miller\n"],
        })
        merged = gm.merge_two_records(primary, secondary)
        assert "1 OCCU Farmer\n" in merged.extra_fields["OCCU"]
        assert "1 OCCU Miller\n" in merged.extra_fields["OCCU"]
        # No duplicates.
        assert merged.extra_fields["OCCU"].count("1 OCCU Farmer\n") == 1

    def test_primary_non_empty_field_not_overwritten(self):
        primary = _make_record(given_name="John")
        secondary = _make_record(given_name="Jonathan")
        merged = gm.merge_two_records(primary, secondary)
        assert merged.given_name == "John"


class TestSyntheticSerialization:
    """Ensure source-less library records retain all serializable evidence."""

    def test_names_facts_and_typed_family_links_are_emitted(self):
        record = _make_record(
            alternate_names=("Jonathan Smith",),
            facts={
                "BIRT": (
                    gm.GenealogicalFact(
                        "BIRT",
                        date="15 JUL 1850",
                        place="Boston, Massachusetts, USA",
                    ),
                    gm.GenealogicalFact("BIRT", date="ABT 1851"),
                ),
                "OCCU": (gm.GenealogicalFact("OCCU", "Carpenter"),),
            },
            family_links=("@F1@", "@F2@"),
            family_references=(("FAMS", "@F1@"), ("FAMC", "@F2@")),
        )
        text = gm._record_to_gedcom_lines(record)
        assert "1 NAME Jonathan Smith" in text
        assert text.count("1 BIRT") == 2
        assert "1 OCCU Carpenter" in text
        assert "1 FAMS @F1@" in text
        assert "1 FAMC @F2@" in text


class TestDependentPreservation:
    """Verify that merging a sparse adult cannot discard collateral people."""

    def test_child_contexts_from_both_records_are_retained(self):
        child_a = gm.RelativeIdentity("@C1@", "Cousin One", "1940")
        child_b = gm.RelativeIdentity("@C2@", "Cousin Two", "1942")
        primary = _make_record(children=(child_a,))
        secondary = _make_record(pointer="@I2@", children=(child_b,))
        merged = gm.merge_two_records(primary, secondary)
        assert {child.pointer for child in merged.children} == {"@C1@", "@C2@"}

    def test_pointer_map_redirects_only_the_duplicate_adult(self):
        child_a = gm.RelativeIdentity("@C1@", "Cousin One", "1940")
        child_b = gm.RelativeIdentity("@C2@", "Cousin Two", "1942")
        primary = _make_record(
            pointer="@P1@",
            children=(child_a,),
            source_file="/a.ged",
        )
        secondary = _make_record(
            pointer="@P2@",
            children=(child_b,),
            source_file="/b.ged",
        )
        first_child = _make_record(
            pointer="@C1@",
            given_name="Cousin",
            surname="One",
            source_file="/a.ged",
        )
        second_child = _make_record(
            pointer="@C2@",
            given_name="Cousin",
            surname="Two",
            source_file="/b.ged",
        )
        pointer_map = {}

        def duplicate(left, right, **kwargs):
            is_parent_pair = {left.pointer, right.pointer} == {"@P1@", "@P2@"}
            return {
                "is_duplicate": is_parent_pair,
                "confidence": 0.99,
                "reasoning": "same adult" if is_parent_pair else "different",
                "preferred_values": {},
            }

        with patch("tools.gedcom_merge.ai_resolve", side_effect=duplicate):
            merged = gm.merge_records(
                [primary, secondary, first_child, second_child],
                threshold=70,
                auto=True,
                pointer_map=pointer_map,
            )
        assert pointer_map["@P2@"] == "@P1@"
        assert pointer_map["@C1@"] == "@C1@"
        assert pointer_map["@C2@"] == "@C2@"
        assert {record.pointer for record in merged} >= {"@P1@", "@C1@", "@C2@"}

    def test_family_records_keep_both_children_after_parent_merge(self, tmp_path):
        parent_a = _make_record(
            pointer="@P1@",
            family_links=("@F1@",),
            family_references=(("FAMS", "@F1@"),),
            source_file="/a.ged",
        )
        parent_b = _make_record(
            pointer="@P2@",
            family_links=("@F2@",),
            family_references=(("FAMS", "@F2@"),),
            source_file="/b.ged",
        )
        child_a = _make_record(
            pointer="@C1@",
            given_name="Cousin",
            surname="One",
            family_links=("@F1@",),
            family_references=(("FAMC", "@F1@"),),
            source_file="/a.ged",
        )
        child_b = _make_record(
            pointer="@C2@",
            given_name="Cousin",
            surname="Two",
            family_links=("@F2@",),
            family_references=(("FAMC", "@F2@"),),
            source_file="/b.ged",
        )
        family_a = _family_record("@F1@", [
            "1 HUSB @P1@",
            "1 CHIL @C1@",
        ])
        family_b = _family_record("@F2@", [
            "1 HUSB @P2@",
            "1 CHIL @C2@",
        ])
        head = gm.GedcomRecord([
            "0 HEAD",
            "1 GEDC",
            "2 VERS 5.5.5",
            "1 CHAR UTF-8",
        ], "/a.ged", 0)
        sources = [gm.ParsedSource(
            Path("/a.ged"),
            [head, family_a, family_b],
            {},
        )]
        pointer_map = {"@P2@": "@P1@"}
        merged_parent = gm.merge_two_records(parent_a, parent_b)
        output = tmp_path / "dependents.ged"
        gm.write_gedcom(
            [merged_parent, child_a, child_b],
            output,
            source_documents=sources,
            pointer_map=pointer_map,
        )
        text = output.read_text(encoding="utf-8")
        assert "0 @C1@ INDI" in text
        assert "0 @C2@ INDI" in text
        assert "1 CHIL @C1@" in text
        assert "1 CHIL @C2@" in text
        assert text.count("1 HUSB @P1@") == 2
        assert "1 FAMS @F1@" in text
        assert "1 FAMS @F2@" in text
        assert "1 FAMC @F1@" in text
        assert "1 FAMC @F2@" in text

    def test_pedigree_qualifier_survives_output(self, tmp_path):
        child = _make_record(
            pointer="@C1@",
            raw_lines=[
                "0 @C1@ INDI",
                "1 NAME Cousin /One/",
                "1 FAMC @F1@",
                "2 PEDI adopted",
            ],
        )
        head = gm.GedcomRecord([
            "0 HEAD",
            "1 GEDC",
            "2 VERS 5.5.5",
            "1 CHAR UTF-8",
        ], "/a.ged", 0)
        family = _family_record("@F1@", ["1 CHIL @C1@"])
        source = gm.ParsedSource(Path("/a.ged"), [head, family], {})
        output = tmp_path / "pedigree.ged"
        gm.write_gedcom([child], output, source_documents=[source])
        text = output.read_text(encoding="utf-8")
        assert "1 FAMC @F1@\n2 PEDI adopted" in text
        assert "1 CHIL @C1@" in text


# ---------------------------------------------------------------------------
# AI response parsing
# ---------------------------------------------------------------------------


class TestParseAiResponse:
    def test_valid_json_is_duplicate_true(self):
        raw = '{"is_duplicate": true, "confidence": 0.95, "reasoning": "same person"}'
        result = gm._parse_ai_response(raw)
        assert result["is_duplicate"] is True
        assert result["confidence"] == pytest.approx(0.95)
        assert result["reasoning"] == "same person"

    def test_valid_json_is_duplicate_false(self):
        raw = '{"is_duplicate": false, "confidence": 0.1, "reasoning": "different"}'
        result = gm._parse_ai_response(raw)
        assert result["is_duplicate"] is False

    def test_markdown_fenced_json(self):
        raw = (
            "```json\n"
            '{"is_duplicate": true, "confidence": 0.9, "reasoning": "ok"}'
            "\n```"
        )
        result = gm._parse_ai_response(raw)
        assert result["is_duplicate"] is True

    def test_json_embedded_in_prose(self):
        raw = (
            "After careful analysis I believe these are the same person.\n"
            '{"is_duplicate": true, "confidence": 0.8, "reasoning": "names match"}\n'
            "Hope that helps!"
        )
        result = gm._parse_ai_response(raw)
        assert result["is_duplicate"] is True

    def test_unparseable_response_returns_safe_default(self):
        result = gm._parse_ai_response("I have no idea what you are asking")
        assert result["is_duplicate"] is False
        assert result["confidence"] == 0.0


# ---------------------------------------------------------------------------
# Remote credit gate (unit - no network)
# ---------------------------------------------------------------------------


class TestRemoteCreditGate:
    """Verify that no decision data can bypass the strict remote gate."""

    def test_direct_openai_is_blocked_under_required_policy(self):
        with pytest.raises(gm.RemoteCreditError, match="Cannot verify openai"):
            gm.ensure_remote_credit(
                "openai",
                api_key="fake-key",
                policy="required",
            )

    def test_direct_provider_can_be_explicitly_best_effort(self):
        status = gm.ensure_remote_credit(
            "gemini",
            api_key="fake-key",
            policy="best-effort",
        )
        assert status.checked is False
        assert status.remaining_usd is None

    def test_openrouter_management_balance_passes(self):
        payload = {
            "data": {"total_credits": 10.0, "total_usage": 2.5},
        }
        with patch("tools.gedcom_merge._get_remote_json", return_value=payload):
            status = gm.ensure_remote_credit(
                "openrouter",
                api_key="inference-key",
                management_key="management-key",
                policy="required",
                minimum_credit_usd=1.0,
            )
        assert status.checked is True
        assert status.remaining_usd == pytest.approx(7.5)

    def test_openrouter_key_limit_is_not_account_balance(self):
        payload = {"data": {"limit_remaining": 5.0}}
        with patch("tools.gedcom_merge._get_remote_json", return_value=payload):
            with pytest.raises(gm.RemoteCreditError, match="not the account"):
                gm.ensure_remote_credit(
                    "openrouter",
                    api_key="inference-key",
                    policy="required",
                )

    def test_openrouter_insufficient_balance_is_blocked(self):
        payload = {
            "data": {"total_credits": 10.0, "total_usage": 9.995},
        }
        with patch("tools.gedcom_merge._get_remote_json", return_value=payload):
            with pytest.raises(gm.RemoteCreditError, match="at least"):
                gm.ensure_remote_credit(
                    "openrouter",
                    api_key="inference-key",
                    management_key="management-key",
                    policy="required",
                    minimum_credit_usd=0.01,
                )


# ---------------------------------------------------------------------------
# Gemini AI resolver (unit – no network)
# ---------------------------------------------------------------------------


class TestAiResolveGemini:
    def test_raises_when_api_key_missing(self):
        a = _make_record(pointer="@I1@", source_file="/a.ged")
        b = _make_record(pointer="@I2@", source_file="/b.ged")
        # Patch google.generativeai into sys.modules to avoid ModuleNotFoundError
        # when the optional google-generativeai package is not installed.
        import types

        fake_genai = types.ModuleType("google.generativeai")
        fake_google = types.ModuleType("google")
        fake_google.generativeai = fake_genai  # type: ignore[attr-defined]
        with (
            patch.dict(
                "sys.modules",
                {"google": fake_google, "google.generativeai": fake_genai},
            ),
            patch.dict("os.environ", {}, clear=True),
        ):
            with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
                gm.ai_resolve_gemini(a, b, api_key=None)

    def test_returns_parsed_verdict(self):
        a = _make_record(pointer="@I1@", source_file="/a.ged")
        b = _make_record(pointer="@I2@", source_file="/b.ged")

        fake_response = MagicMock()
        fake_response.text = (
            '{"is_duplicate": true, "confidence": 0.92, '
            '"reasoning": "same"}'
        )

        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_response

        import types

        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model
        fake_google = types.ModuleType("google")
        fake_google.generativeai = fake_genai  # type: ignore[attr-defined]

        with patch.dict(
            "sys.modules",
            {"google": fake_google, "google.generativeai": fake_genai},
        ), patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}):
            result = gm.ai_resolve_gemini(
                a,
                b,
                credit_policy="best-effort",
            )

        assert result["is_duplicate"] is True
        assert result["confidence"] == pytest.approx(0.92)


# ---------------------------------------------------------------------------
# Merge orchestration (unit – AI mocked)
# ---------------------------------------------------------------------------


class TestMergeRecords:
    def test_auto_merges_score_above_95(self):
        # Two identical records from different files should be auto-merged.
        a = _make_record(pointer="@I1@", source_file="/a.ged")
        b = _make_record(pointer="@I2@", source_file="/b.ged")
        # Score will be 100, above the 95 auto-merge threshold.
        result = gm.merge_records([a, b], threshold=80, auto=True)
        assert len(result) == 1
        assert result[0].pointer == "@I1@"

    def test_different_individuals_not_merged(self):
        a = _make_record(
            pointer="@I1@",
            given_name="John",
            surname="Smith",
            birth_date="1850",
            source_file="/a.ged",
        )
        b = _make_record(
            pointer="@I2@",
            given_name="Mary",
            surname="Jones",
            birth_date="1780",
            source_file="/b.ged",
        )
        result = gm.merge_records([a, b], threshold=80, auto=True)
        assert len(result) == 2

    def test_ai_verdict_applied_when_auto(self):
        # Two records with a similarity score in the 80–94 range; the AI
        # should be consulted and, because auto=True, its verdict applied.
        a = _make_record(
            pointer="@I1@",
            given_name="John",
            surname="Smith",
            birth_date="1850",
            source_file="/a.ged",
        )
        b = _make_record(
            pointer="@I2@",
            given_name="John",
            surname="Smyth",  # slight spelling difference
            birth_date="1851",
            source_file="/b.ged",
        )

        def fake_ai_resolve(rec_a, rec_b, backend="ollama", **kwargs):
            return {"is_duplicate": True, "confidence": 0.9, "reasoning": "same"}

        with patch("tools.gedcom_merge.ai_resolve", side_effect=fake_ai_resolve):
            result = gm.merge_records([a, b], threshold=70, auto=True)

        assert len(result) == 1

    def test_ai_error_falls_back_without_merge(self):
        # When AI raises, the pair should NOT be merged (safe default).
        a = _make_record(
            pointer="@I1@",
            given_name="John",
            surname="Smith",
            birth_date="1850",
            source_file="/a.ged",
        )
        b = _make_record(
            pointer="@I2@",
            given_name="John",
            surname="Smyth",
            birth_date="1851",
            source_file="/b.ged",
        )

        def fail_ai(rec_a, rec_b, backend="ollama", **kwargs):
            raise RuntimeError("Ollama offline")

        with patch("tools.gedcom_merge.ai_resolve", side_effect=fail_ai):
            # auto=True so operator prompt is bypassed; AI error → no merge.
            result = gm.merge_records([a, b], threshold=70, auto=True)

        assert len(result) == 2


# ---------------------------------------------------------------------------
# GEDCOM serialisation (synthetic records)
# ---------------------------------------------------------------------------


class TestRecordToGedcomLines:
    def test_basic_serialisation(self):
        rec = _make_record(pointer="@I42@", given_name="Alice", surname="Brown")
        lines = gm._record_to_gedcom_lines(rec)
        assert "0 @I42@ INDI" in lines
        assert "NAME Alice /Brown/" in lines

    def test_birth_and_death_included(self):
        rec = _make_record(birth_date="1850", death_date="1920")
        lines = gm._record_to_gedcom_lines(rec)
        assert "1 BIRT" in lines
        assert "2 DATE 1850" in lines
        assert "1 DEAT" in lines
        assert "2 DATE 1920" in lines

    def test_gender_included(self):
        rec = _make_record(gender="F")
        lines = gm._record_to_gedcom_lines(rec)
        assert "1 SEX F" in lines


# ---------------------------------------------------------------------------
# GEDCOM file writing (synthetic, no disk I/O during assertion)
# ---------------------------------------------------------------------------


class TestWriteGedcom:
    def test_creates_output_file(self, tmp_path):
        records = [_make_record(pointer="@I1@")]
        out = tmp_path / "out.ged"
        gm.write_gedcom(records, out, source_parsers=None)
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "0 TRLR" in content

    def test_output_contains_individuals(self, tmp_path):
        records = [
            _make_record(pointer="@I1@", given_name="Alice", surname="Brown"),
            _make_record(pointer="@I2@", given_name="Bob", surname="Brown"),
        ]
        out = tmp_path / "out.ged"
        gm.write_gedcom(records, out, source_parsers=None)
        content = out.read_text(encoding="utf-8")
        assert "@I1@" in content
        assert "@I2@" in content

    def test_output_file_has_header(self, tmp_path):
        out = tmp_path / "out.ged"
        gm.write_gedcom([], out, source_parsers=None)
        content = out.read_text(encoding="utf-8")
        assert "0 HEAD" in content


# ---------------------------------------------------------------------------
# CLI argument parsing (no file I/O)
# ---------------------------------------------------------------------------


class TestBuildArgParser:
    def test_default_output_is_merged_ged(self):
        ap = gm._build_arg_parser()
        args = ap.parse_args(["a.ged", "b.ged"])
        assert args.output == "merged.ged"

    def test_custom_output(self):
        ap = gm._build_arg_parser()
        args = ap.parse_args(["a.ged", "b.ged", "-o", "master.ged"])
        assert args.output == "master.ged"

    def test_ai_backend_default_is_ollama(self):
        ap = gm._build_arg_parser()
        args = ap.parse_args(["a.ged", "b.ged"])
        assert args.ai_backend == "ollama"

    def test_ai_backend_gemini(self):
        ap = gm._build_arg_parser()
        args = ap.parse_args(["a.ged", "b.ged", "--ai-backend", "gemini"])
        assert args.ai_backend == "gemini"

    def test_ai_backend_openrouter(self):
        ap = gm._build_arg_parser()
        args = ap.parse_args(
            ["a.ged", "b.ged", "--ai-backend", "openrouter"]
        )
        assert args.ai_backend == "openrouter"

    def test_credit_check_defaults_to_required(self):
        ap = gm._build_arg_parser()
        args = ap.parse_args(["a.ged", "b.ged"])
        assert args.credit_check == "required"

    def test_auto_flag(self):
        ap = gm._build_arg_parser()
        args = ap.parse_args(["a.ged", "b.ged", "--auto"])
        assert args.auto is True

    def test_similarity_threshold_custom(self):
        ap = gm._build_arg_parser()
        args = ap.parse_args(["a.ged", "b.ged", "--similarity-threshold", "75"])
        assert args.similarity_threshold == 75


# ---------------------------------------------------------------------------
# CLI main() integration (file I/O mocked)
# ---------------------------------------------------------------------------


class TestMainCli:
    def test_returns_1_when_only_one_file(self, capsys):
        # main() calls ap.error() which raises SystemExit(2) when fewer than
        # two files are given.
        with pytest.raises(SystemExit) as exc_info:
            gm.main(["only_one.ged"])
        assert exc_info.value.code != 0

    def test_returns_1_when_file_not_found(self, tmp_path):
        result = gm.main(
            [
                str(tmp_path / "nonexistent_a.ged"),
                str(tmp_path / "nonexistent_b.ged"),
            ]
        )
        assert result == 1

    def test_returns_0_on_successful_merge(self, tmp_path):
        """Full integration: write two minimal GEDCOM files and merge them."""
        gedcom_a = textwrap.dedent("""\
            0 HEAD
            1 SOUR Test
            0 @I1@ INDI
            1 NAME Alice /Brown/
            1 SEX F
            1 BIRT
            2 DATE 1850
            0 TRLR
        """)
        gedcom_b = textwrap.dedent("""\
            0 HEAD
            1 SOUR Test
            0 @I2@ INDI
            1 NAME Bob /Green/
            1 SEX M
            1 BIRT
            2 DATE 1855
            0 TRLR
        """)
        file_a = tmp_path / "a.ged"
        file_b = tmp_path / "b.ged"
        file_a.write_text(gedcom_a, encoding="utf-8")
        file_b.write_text(gedcom_b, encoding="utf-8")
        out = tmp_path / "merged.ged"

        result = gm.main(
            [
                str(file_a), str(file_b), "-o", str(out), "--auto",
                "--no-quality-report",
            ]
        )
        assert result == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "0 TRLR" in content

    def test_merges_duplicates_in_two_files(self, tmp_path):
        """Two files with the same person should produce one output individual."""
        gedcom_template = textwrap.dedent("""\
            0 HEAD
            1 SOUR Test
            0 {ptr} INDI
            1 NAME John /Smith/
            1 SEX M
            1 BIRT
            2 DATE 15 JUL 1850
            1 DEAT
            2 DATE 01 JAN 1920
            0 TRLR
        """)
        file_a = tmp_path / "a.ged"
        file_b = tmp_path / "b.ged"
        file_a.write_text(gedcom_template.format(ptr="@I1@"), encoding="utf-8")
        file_b.write_text(gedcom_template.format(ptr="@I2@"), encoding="utf-8")
        out = tmp_path / "merged.ged"

        result = gm.main(
            [
                str(file_a), str(file_b), "-o", str(out), "--auto",
                "--no-quality-report",
            ]
        )
        assert result == 0
        content = out.read_text(encoding="utf-8")
        # Only one INDI block should appear in the output.
        assert content.count("INDI") == 1
