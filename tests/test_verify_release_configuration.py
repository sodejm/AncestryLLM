from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
_SCRIPT = ROOT / "scripts" / "verify_release_configuration.py"
_SPEC = importlib.util.spec_from_file_location("verify_release_configuration", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
verifier = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = verifier
_SPEC.loader.exec_module(verifier)


def _configuration() -> dict[str, object]:
    return {
        "schema_version": 1,
        "release": "0.4.0",
        "milestone": {"number": 3, "title": "0.4.0 Genealogy Core Facades"},
        "tracker": {"number": 193, "label": "release-tracker"},
    }


def test_accepts_exact_release_control_configuration() -> None:
    configuration = verifier.validate_release_configuration(
        _configuration(),
        expected_version="0.4.0",
    )

    assert configuration.release == "0.4.0"
    assert configuration.milestone_number == 3
    assert configuration.milestone_title == "0.4.0 Genealogy Core Facades"
    assert configuration.tracker_number == 193
    assert configuration.tracker_label == "release-tracker"


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ({"release": "0.3.0"}, "does not match"),
        ({"release": "0.3"}, "stable SemVer"),
        ({"schema_version": 2}, "schema_version"),
        ({"unexpected": True}, "keys are invalid"),
        (
            {"milestone": {"number": True, "title": "0.4.0 Genealogy Core Facades"}},
            "positive integer",
        ),
        ({"tracker": {"number": 193, "label": "release-tracker\n"}}, "trimmed string"),
    ),
)
def test_rejects_mismatched_or_malformed_configuration(
    mutation: dict[str, object],
    message: str,
) -> None:
    configuration = _configuration()
    configuration.update(mutation)

    with pytest.raises(ValueError, match=message):
        verifier.validate_release_configuration(
            configuration,
            expected_version="0.4.0",
        )


def test_cli_rejects_invalid_json(tmp_path: Path) -> None:
    configuration = tmp_path / "release.json"
    configuration.write_text("{", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--config",
            str(configuration),
            "--version",
            "0.4.0",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert completed.returncode == 1
    assert "release configuration verification failed" in completed.stderr


def test_repository_configuration_passes_cli_validation() -> None:
    configuration = ROOT / ".github" / "release-config.json"
    payload = json.loads(configuration.read_text(encoding="utf-8"))

    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--config",
            str(configuration),
            "--version",
            str(payload["release"]),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
