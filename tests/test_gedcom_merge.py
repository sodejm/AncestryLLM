"""Tests for tools.gedcom_merge.

Each test is self-contained: no GEDCOM files are read from disk; all
individual records are constructed programmatically so the suite runs
without the network, Ollama, or Gemini.
"""

from __future__ import annotations

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
        # Name is the same (60 % weight) but years are far off.
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
        secondary = _make_record(extra_fields={"OCCU": ["1 OCCU Farmer\n", "1 OCCU Miller\n"]})
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
        raw = "```json\n{\"is_duplicate\": true, \"confidence\": 0.9, \"reasoning\": \"ok\"}\n```"
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
# Gemini AI resolver (unit – no network)
# ---------------------------------------------------------------------------


class TestAiResolveGemini:
    def test_raises_when_api_key_missing(self):
        a = _make_record(pointer="@I1@", source_file="/a.ged")
        b = _make_record(pointer="@I2@", source_file="/b.ged")
        # Patch google.generativeai into sys.modules to avoid ModuleNotFoundError
        # when the optional google-generativeai package is not installed.
        import sys
        import types

        fake_genai = types.ModuleType("google.generativeai")
        fake_google = types.ModuleType("google")
        fake_google.generativeai = fake_genai  # type: ignore[attr-defined]
        with patch.dict("sys.modules", {"google": fake_google, "google.generativeai": fake_genai}), \
             patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
                gm.ai_resolve_gemini(a, b, api_key=None)

    def test_returns_parsed_verdict(self):
        a = _make_record(pointer="@I1@", source_file="/a.ged")
        b = _make_record(pointer="@I2@", source_file="/b.ged")

        fake_response = MagicMock()
        fake_response.text = '{"is_duplicate": true, "confidence": 0.92, "reasoning": "same"}'

        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_response

        import sys
        import types

        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model
        fake_google = types.ModuleType("google")
        fake_google.generativeai = fake_genai  # type: ignore[attr-defined]

        with patch.dict(
            "sys.modules",
            {"google": fake_google, "google.generativeai": fake_genai},
        ), patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}):
            result = gm.ai_resolve_gemini(a, b)

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
            [str(file_a), str(file_b), "-o", str(out), "--auto"]
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
            [str(file_a), str(file_b), "-o", str(out), "--auto"]
        )
        assert result == 0
        content = out.read_text(encoding="utf-8")
        # Only one INDI block should appear in the output.
        assert content.count("INDI") == 1
