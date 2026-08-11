"""Contracts for the isolated setuptools versus uv_build evaluation."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest
from scripts import evaluate_uv_build as evaluation

ROOT = Path(__file__).resolve().parents[1]


def _zip(path: Path, files: list[tuple[str, bytes]], *, year: int) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files:
            info = zipfile.ZipInfo(name, date_time=(year, 1, 2, 3, 4, 6))
            archive.writestr(info, data)


def _tar(path: Path, files: list[tuple[str, bytes]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


def _record(files: dict[str, bytes], record_name: str) -> bytes:
    rows: list[list[str]] = []
    for name, data in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        rows.append([name, f"sha256={digest}", str(len(data))])
    rows.append([record_name, "", ""])
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue().encode()


def test_evaluation_contract_keeps_setuptools_authoritative() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert project["build-system"] == {
        "requires": ["setuptools>=83"],
        "build-backend": "setuptools.build_meta",
    }
    assert "uv_build>=0.12.0,<0.13" in project["dependency-groups"]["build"]
    assert "evaluate-uv-build: verified-uv" in makefile
    assert "scripts/evaluate_uv_build.py --uv $(UV_BIN) --report $(UV_BUILD_REPORT)" in makefile


@pytest.mark.parametrize(
    "output",
    (
        "uv 0.12.1",
        "uv 0.12.1 (329541a50 2026-07-31 aarch64-apple-darwin)",
    ),
)
def test_evaluation_accepts_only_the_pinned_uv_release(output: str) -> None:
    evaluation.validate_uv_version(output)


@pytest.mark.parametrize(
    "output",
    (
        "uv 0.12.0",
        "uv 0.12.10",
        "uv 0.12.1 unreviewed",
        "uv latest",
    ),
)
def test_evaluation_rejects_other_or_malformed_uv_versions(output: str) -> None:
    with pytest.raises(evaluation.EvaluationError) as error:
        evaluation.validate_uv_version(output)

    assert error.value.code == "UVBEVAL_UV_VERSION"


def test_candidate_overlay_changes_only_the_reviewed_build_configuration() -> None:
    original = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    candidate = evaluation.candidate_pyproject(original)

    assert '[build-system]\nrequires = ["uv_build>=0.12.0,<0.13"]' in candidate
    assert 'build-backend = "uv_build"' in candidate
    assert "[tool.uv.build-backend]" in candidate
    assert evaluation.CANDIDATE_SOURCE_INCLUDES == [
        "CHANGELOG.md",
        "MANIFEST.in",
        "docs/CLI.md",
        "docs/CONSOLE.md",
        "docs/FILE_INGRESS.md",
        "docs/GEDCOM_COMPATIBILITY.md",
        "docs/PROVIDERS.md",
        "docs/RELEASING.md",
        "docs/SETUP_DIAGNOSTICS.md",
        "docs/VERSIONING.md",
        "docs/release-evidence/README.md",
        "docs/release-evidence/issue-10-import-smoke-tests.md",
    ]
    assert original.startswith('[build-system]\nrequires = ["setuptools>=83"]')
    assert (ROOT / "pyproject.toml").read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "member_name",
    (
        "../escape.py",
        "/absolute.py",
        "root/../../escape.py",
        "root\\..\\escape.py",
    ),
)
def test_archive_readers_reject_unsafe_members(tmp_path: Path, member_name: str) -> None:
    wheel = tmp_path / "unsafe.whl"
    sdist = tmp_path / "unsafe.tar.gz"
    _zip(wheel, [(member_name, b"unsafe")], year=2024)
    _tar(sdist, [(member_name, b"unsafe")])

    with pytest.raises(evaluation.EvaluationError, match="unsafe archive member") as wheel_error:
        evaluation.read_zip_files(wheel)
    with pytest.raises(evaluation.EvaluationError, match="unsafe archive member") as sdist_error:
        evaluation.read_sdist_files(sdist)

    assert wheel_error.value.code == "UVBEVAL_UNSAFE_ARCHIVE"
    assert sdist_error.value.code == "UVBEVAL_UNSAFE_ARCHIVE"


def test_archive_snapshots_ignore_order_and_timestamps_but_not_payload(tmp_path: Path) -> None:
    first = tmp_path / "first.whl"
    second = tmp_path / "second.whl"
    changed = tmp_path / "changed.whl"
    files = [("ancestryllm/__init__.py", b"VALUE = 1\n"), ("data.txt", b"same\n")]
    _zip(first, files, year=2024)
    _zip(second, list(reversed(files)), year=2025)
    _zip(changed, [(files[0][0], b"VALUE = 2\n"), files[1]], year=2025)

    first_files = evaluation.read_zip_files(first)
    second_files = evaluation.read_zip_files(second)
    changed_files = evaluation.read_zip_files(changed)

    assert evaluation.compare_file_maps(first_files, second_files) == {
        "added": [],
        "changed": [],
        "removed": [],
    }
    assert evaluation.compare_file_maps(first_files, changed_files) == {
        "added": [],
        "changed": ["ancestryllm/__init__.py"],
        "removed": [],
    }


def test_semantic_metadata_ignores_header_order_but_keeps_values_exact() -> None:
    first = b"Metadata-Version: 2.4\nName: ancestryllm\nRequires-Dist: beta\nRequires-Dist: alpha\n\nbody\n"
    reordered = b"Name: ancestryllm\nRequires-Dist: alpha\nMetadata-Version: 2.4\nRequires-Dist: beta\n\nbody\n"
    changed = reordered.replace(b"Name: ancestryllm", b"Name: other")

    assert evaluation.semantic_message(first) == evaluation.semantic_message(reordered)
    assert evaluation.semantic_message(first) != evaluation.semantic_message(changed)


def test_record_validation_checks_every_hash_and_size() -> None:
    record_name = "ancestryllm-0.5.0.dist-info/RECORD"
    files = {
        "ancestryllm/__init__.py": b"value = 1\n",
        "ancestryllm-0.5.0.dist-info/METADATA": b"Name: ancestryllm\n",
    }
    complete = {**files, record_name: _record(files, record_name)}

    assert evaluation.validate_record(complete) == sorted(complete)

    corrupt = {**complete, "ancestryllm/__init__.py": b"value = 2\n"}
    with pytest.raises(evaluation.EvaluationError) as error:
        evaluation.validate_record(corrupt)
    assert error.value.code == "UVBEVAL_INVALID_RECORD"


def test_report_serialization_is_deterministic_and_rejects_local_paths() -> None:
    report = {
        "evaluation": "setuptools-vs-uv_build",
        "failure_codes": ["UVBEVAL_COMMAND_FAILED"],
        "schema_version": 1,
        "status": "error",
    }

    first = evaluation.serialize_report(report)
    second = evaluation.serialize_report(report)

    assert first == second
    assert first.endswith("\n")
    assert json.loads(first) == report

    report["failure_codes"] = [f"UVBEVAL_{evaluation.ROOT}"]
    with pytest.raises(evaluation.EvaluationError) as error:
        evaluation.serialize_report(report)
    assert error.value.code == "UVBEVAL_UNSAFE_REPORT"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ({"schema_version": 2}, "UVBEVAL_REPORT_SCHEMA"),
        ({"unknown": "value"}, "UVBEVAL_REPORT_FIELDS"),
    ),
)
def test_report_schema_rejects_unknown_versions_and_fields(
    mutation: dict[str, object], expected_code: str
) -> None:
    report = {
        "evaluation": "setuptools-vs-uv_build",
        "failure_codes": ["UVBEVAL_COMMAND_FAILED"],
        "schema_version": 1,
        "status": "error",
        **mutation,
    }

    with pytest.raises(evaluation.EvaluationError) as error:
        evaluation.serialize_report(report)

    assert error.value.code == expected_code


def test_report_schema_rejects_missing_fields() -> None:
    report = {
        "evaluation": "setuptools-vs-uv_build",
        "schema_version": 1,
        "status": "error",
    }

    with pytest.raises(evaluation.EvaluationError) as error:
        evaluation.serialize_report(report)

    assert error.value.code == "UVBEVAL_REPORT_FIELDS"


def test_report_schema_documents_the_only_output_normalizations() -> None:
    assert evaluation.ACCEPTED_OUTPUT_NORMALIZATIONS == [
        {
            "code": "UVBEVAL_ARCHIVE_MEMBER_ORDER",
            "scope": ["sdist", "wheel"],
        },
        {
            "code": "UVBEVAL_ARCHIVE_METADATA_TIMESTAMPS",
            "scope": ["sdist", "wheel"],
        },
    ]
