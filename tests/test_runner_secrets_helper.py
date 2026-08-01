from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPOSITORY_ROOT / "scripts" / "ancestryll-runner-secrets-helper.sh"

SECRET_NAMES = {
    "APPLE_CERTIFICATE_BASE64",
    "APPLE_CERTIFICATE_PASSWORD",
    "APPLE_API_KEY_BASE64",
    "APPLE_API_KEY_ID",
    "APPLE_API_ISSUER",
    "WINDOWS_CERTIFICATE_BASE64",
    "WINDOWS_CERTIFICATE_PASSWORD",
    "LINUX_GPG_PRIVATE_KEY_BASE64",
    "LINUX_GPG_PASSPHRASE",
}

VARIABLE_VALUES = {
    "APPLE_TEAM_ID": "ABCDEFGHIJ",
    "WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT": "A" * 40,
    "LINUX_GPG_SIGNING_FINGERPRINT": "B" * 40,
}


def _write_fake_gh(path: Path) -> None:
    path.write_text(
        """#!/bin/sh
set -eu

case "$1:$2" in
  auth:status|repo:view)
    exit 0
    ;;
  secret:set)
    name=$3
    mkdir -p "$FAKE_GH_STATE/secrets"
    : > "$FAKE_GH_STATE/secrets/$name"
    # Consume but never persist a private value.
    dd of=/dev/null 2>/dev/null
    ;;
  secret:list)
    find "$FAKE_GH_STATE/secrets" -type f -exec basename {} \\; | sort
    ;;
  variable:set)
    name=$3
    shift 3
    mkdir -p "$FAKE_GH_STATE/variables"
    body=''
    found_body=false
    while [ "$#" -gt 0 ]; do
      if [ "$1" = '--body' ]; then
        body=$2
        found_body=true
        shift 2
      else
        shift
      fi
    done
    if [ "$found_body" = true ]; then
      printf '%s' "$body" > "$FAKE_GH_STATE/variables/$name"
    else
      dd of="$FAKE_GH_STATE/variables/$name" 2>/dev/null
    fi
    ;;
  variable:list)
    find "$FAKE_GH_STATE/variables" -type f -exec basename {} \\; | sort
    ;;
  variable:get)
    name=$3
    dd if="$FAKE_GH_STATE/variables/$name" 2>/dev/null
    ;;
  *)
    printf 'unexpected fake gh call: %s\n' "$*" >&2
    exit 2
    ;;
esac
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_helper_uploads_and_verifies_every_configured_value(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_gh(fake_bin / "gh")

    state = tmp_path / "state"
    state.mkdir()

    payloads = []
    for index in range(5):
        payload = tmp_path / f"payload-{index}.bin"
        payload.write_bytes(f"fictional signing payload {index}\n".encode())
        payloads.append(payload)

    private_values = [
        "apple-certificate-password",
        "apple-api-key-id",
        "apple-api-issuer",
        "windows-certificate-password",
        "linux-gpg-passphrase",
    ]
    prompt_input = [*(str(path) for path in payloads)]
    for value in private_values:
        prompt_input.extend((value, value))
    prompt_input.extend(VARIABLE_VALUES.values())
    prompt_input.append("UPLOAD")

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_GH_STATE"] = str(state)
    result = subprocess.run(
        [str(HELPER), "--upload"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        input="\n".join(prompt_input) + "\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert {path.name for path in (state / "secrets").iterdir()} == SECRET_NAMES

    expected_variables = dict(VARIABLE_VALUES)
    expected_variables["LINUX_GPG_PUBLIC_KEY_BASE64"] = (
        subprocess.run(
            ["/usr/bin/base64"],
            input=payloads[-1].read_bytes(),
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .rstrip("\n")
    )
    observed_variables = {
        path.name: path.read_text(encoding="utf-8").rstrip("\n")
        for path in (state / "variables").iterdir()
    }
    assert observed_variables == expected_variables

    combined_output = result.stdout + result.stderr
    assert "UPLOAD COMPLETE" in combined_output
    assert all(value not in combined_output for value in private_values)


def test_helper_help_uses_the_comma_free_name() -> None:
    result = subprocess.run(
        [str(HELPER), "--help"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "ancestryll-runner-secrets-helper.sh" in result.stdout
    assert "ancestryll,-runner-secrets-helper.sh" not in result.stdout


def test_helper_rejects_credential_sources_inside_repository(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_gh(fake_bin / "gh")

    state = tmp_path / "state"
    state.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_GH_STATE"] = str(state)

    result = subprocess.run(
        [str(HELPER), "--dry-run"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        input=f"{REPOSITORY_ROOT / 'pyproject.toml'}\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Credential source files must be stored outside the repository" in result.stderr
