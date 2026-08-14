"""Verify CodeQL SARIF validation and release-evidence result handling."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_codeql_sarif.py"
_SPEC = importlib.util.spec_from_file_location("verify_codeql_sarif", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
verifier = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = verifier
_SPEC.loader.exec_module(verifier)


def _sarif(*results: object) -> dict[str, object]:
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "CodeQL"}},
                "results": list(results),
            }
        ],
    }


def test_accepts_one_or_more_valid_zero_result_sarif_files(tmp_path: Path) -> None:
    first = tmp_path / "python.sarif"
    first.write_text(json.dumps(_sarif()), encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    second = nested / "actions.sarif"
    second.write_text(json.dumps(_sarif()), encoding="utf-8")

    assert verifier.verify_codeql_sarif(tmp_path) == (second, first)


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ([], "root must be an object"),
        ({"version": "2.0.0", "runs": []}, "unsupported SARIF version"),
        ({"version": "2.1.0", "runs": []}, "runs must be a non-empty list"),
        (
            {"version": "2.1.0", "runs": [{"tool": {}, "results": []}]},
            "must identify its tool driver",
        ),
        (
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {"driver": {"name": "CodeQL"}},
                        "results": {},
                    }
                ],
            },
            "results must be a list",
        ),
    ),
)
def test_rejects_structurally_invalid_sarif(
    tmp_path: Path,
    payload: object,
    message: str,
) -> None:
    (tmp_path / "invalid.sarif").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        verifier.verify_codeql_sarif(tmp_path)


def test_rejects_missing_or_malformed_sarif_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="produced no SARIF"):
        verifier.verify_codeql_sarif(tmp_path)

    (tmp_path / "invalid.sarif").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid UTF-8 SARIF JSON"):
        verifier.verify_codeql_sarif(tmp_path)


def test_rejects_every_result_without_disclosing_result_payload(tmp_path: Path) -> None:
    source = tmp_path / "python.sarif"
    source.write_text(
        json.dumps(
            _sarif(
                {"ruleId": "fictional-one", "message": {"text": "private payload"}},
                {"ruleId": "fictional-two"},
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as raised:
        verifier.verify_codeql_sarif(tmp_path)

    assert str(raised.value) == "CodeQL SARIF contains 2 undispositioned result(s)"
    assert "private payload" not in str(raised.value)
