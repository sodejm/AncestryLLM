from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPOSITORY_ROOT / "scripts" / "ancestryll-runner-secrets-helper.sh"
GITHUB_HOST = "github.com"
APPROVED_GITHUB_ACCOUNT = "sodejm"
REPOSITORY = "sodejm/AncestryLLM"
SIGNING_ENVIRONMENT = "desktop-signing"
UPLOAD_CONFIRMATION = (
    f"UPLOAD {GITHUB_HOST}/{REPOSITORY} {SIGNING_ENVIRONMENT} AS {APPROVED_GITHUB_ACCOUNT}"
)

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

APPLE_DEVELOPER_ID_SHA1 = "A" * 40


def _write_fake_apple_tools(path: Path, identities: str) -> None:
    (path / "uname").write_text(
        """#!/bin/sh
set -eu

[ "$*" = "-s" ] || exit 2
printf 'Darwin\\n'
""",
        encoding="utf-8",
    )
    (path / "uname").chmod((path / "uname").stat().st_mode | stat.S_IXUSR)

    (path / "security").write_text(
        f"""#!/bin/sh
set -eu

if [ "$*" = "find-identity -v -p codesigning" ]; then
  cat <<'EOF'
{identities}
EOF
  exit 0
fi

printf 'unexpected fake security call: %s\n' "$*" >&2
exit 2
""",
        encoding="utf-8",
    )
    (path / "security").chmod((path / "security").stat().st_mode | stat.S_IXUSR)

    (path / "xcrun").write_text(
        """#!/bin/sh
set -eu

[ "$1" = swift ] || exit 2
shift
[ -f "$1" ] || exit 2
shift
[ "$1" = --identity-name ] || exit 2
identity_name=$2
shift 2
[ "$1" = --output ] || exit 2
output=$2
IFS= read -r password
[ -n "$password" ] || exit 2
printf 'fictional selected PKCS12 payload\n' > "$output"
printf '%s' "$identity_name" > "$FAKE_GH_STATE/apple-identity-name"
""",
        encoding="utf-8",
    )
    (path / "xcrun").chmod((path / "xcrun").stat().st_mode | stat.S_IXUSR)


def _valid_keychain_identities() -> str:
    return f"""  1) {"D" * 40} "Apple Development: Fictional Developer (ZYXWVUTSRQ)"
  2) {APPLE_DEVELOPER_ID_SHA1} "Developer ID Application: Fictional Developer ({VARIABLE_VALUES["APPLE_TEAM_ID"]})"
     2 valid identities found"""


def _write_fake_gh(path: Path) -> None:
    path.write_text(
        """#!/bin/sh
set -eu
printf '%s\\t%s\\n' "${GH_HOST-}" "$*" >> "$FAKE_GH_STATE/gh-calls"

case "${1-}:${2-}" in
  --version:)
    printf 'gh version 2.96.0 (fictional test build)\n'
    ;;
  auth:status)
    exit 0
    ;;
  api:--hostname)
    [ "$3" = github.com ] || exit 2
    [ "$4" = user ] || exit 2
    printf '%s\n' "${FAKE_GH_ACCOUNT:-sodejm}"
    ;;
  repo:view)
    printf '%s\n' "${FAKE_GH_REPOSITORY:-sodejm/AncestryLLM}"
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
    _write_fake_apple_tools(fake_bin, _valid_keychain_identities())

    state = tmp_path / "state"
    state.mkdir()

    payloads = []
    for index in range(4):
        payload = tmp_path / f"payload-{index}.bin"
        payload.write_bytes(f"fictional signing payload {index}\n".encode())
        payload.chmod(0o600)
        payloads.append(payload)

    private_values = [
        "apple-api-key-id",
        "apple-api-issuer",
        "windows-certificate-password",
        "linux-gpg-passphrase",
    ]
    prompt_input = [*(str(path) for path in payloads)]
    for value in private_values:
        prompt_input.extend((value, value))
    prompt_input.extend(
        (
            VARIABLE_VALUES["WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT"],
            VARIABLE_VALUES["LINUX_GPG_SIGNING_FINGERPRINT"],
        )
    )
    prompt_input.append(UPLOAD_CONFIRMATION)

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_GH_STATE"] = str(state)
    result = subprocess.run(
        [str(HELPER), "--upload", "--gh-executable", str(fake_bin / "gh")],
        cwd=REPOSITORY_ROOT,
        env=environment,
        input="\n".join(prompt_input) + "\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert {path.name for path in (state / "secrets").iterdir()} == SECRET_NAMES
    assert (state / "apple-identity-name").read_text(encoding="utf-8") == (
        "Developer ID Application: Fictional Developer (ABCDEFGHIJ)"
    )

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
    calls = (state / "gh-calls").read_text(encoding="utf-8").splitlines()
    assert calls
    assert all(call.split("\t", maxsplit=1)[0] == GITHUB_HOST for call in calls)
    assert any("auth status --hostname github.com" in call for call in calls)
    assert f"Authenticated GitHub account: {APPROVED_GITHUB_ACCOUNT}" in combined_output


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
        [
            str(HELPER),
            "--dry-run",
            "--gh-executable",
            str(fake_bin / "gh"),
            "--apple-certificate-file",
            str(REPOSITORY_ROOT / "pyproject.toml"),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        input=f"{REPOSITORY_ROOT / 'pyproject.toml'}\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Credential source files must be stored outside the repository" in result.stderr


def test_helper_fails_when_keychain_has_no_valid_developer_id_application(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_gh(fake_bin / "gh")
    _write_fake_apple_tools(
        fake_bin,
        f"""  1) {"D" * 40} "Apple Development: Fictional Developer (ZYXWVUTSRQ)"
     1 valid identities found""",
    )

    state = tmp_path / "state"
    state.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_GH_STATE"] = str(state)

    result = subprocess.run(
        [
            str(HELPER),
            "--dry-run",
            "--gh-executable",
            str(fake_bin / "gh"),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        input="",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "No valid Developer ID Application identity" in result.stderr
    assert not (state / "apple-identity-name").exists()


def test_helper_fails_closed_for_multiple_developer_id_application_identities(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_gh(fake_bin / "gh")
    _write_fake_apple_tools(
        fake_bin,
        f"""  1) {APPLE_DEVELOPER_ID_SHA1} "Developer ID Application: First Developer (ABCDEFGHIJ)"
  2) {"B" * 40} "Developer ID Application: Second Developer (KLMNOPQRST)"
     2 valid identities found""",
    )

    state = tmp_path / "state"
    state.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_GH_STATE"] = str(state)

    result = subprocess.run(
        [
            str(HELPER),
            "--dry-run",
            "--gh-executable",
            str(fake_bin / "gh"),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        input="",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Multiple valid Developer ID Application identities" in result.stderr
    assert not (state / "apple-identity-name").exists()


def test_helper_never_executes_a_path_shadowing_github_cli(tmp_path: Path) -> None:
    trusted_bin = tmp_path / "trusted-bin"
    trusted_bin.mkdir()
    trusted_gh = trusted_bin / "gh"
    _write_fake_gh(trusted_gh)

    hostile_bin = tmp_path / "hostile-bin"
    hostile_bin.mkdir()
    hostile_marker = tmp_path / "hostile-gh-ran"
    hostile_gh = hostile_bin / "gh"
    hostile_gh.write_text(
        f"#!/bin/sh\nprintf compromised > {hostile_marker}\nexit 97\n",
        encoding="utf-8",
    )
    hostile_gh.chmod(hostile_gh.stat().st_mode | stat.S_IXUSR)

    state = tmp_path / "state"
    state.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = f"{hostile_bin}:{environment['PATH']}"
    environment["FAKE_GH_STATE"] = str(state)

    result = subprocess.run(
        [
            str(HELPER),
            "--dry-run",
            "--gh-executable",
            str(trusted_gh),
            "--apple-certificate-file",
            str(REPOSITORY_ROOT / "pyproject.toml"),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 97
    assert not hostile_marker.exists()
    assert f"GitHub CLI executable: {trusted_gh.resolve()}" in result.stdout
    assert "gh version 2.96.0 (fictional test build)" in result.stdout


def test_helper_rejects_an_explicit_github_cli_symlink(tmp_path: Path) -> None:
    trusted_bin = tmp_path / "trusted-bin"
    trusted_bin.mkdir()
    trusted_gh = trusted_bin / "gh"
    _write_fake_gh(trusted_gh)
    gh_symlink = tmp_path / "gh-symlink"
    gh_symlink.symlink_to(trusted_gh)

    result = subprocess.run(
        [
            str(HELPER),
            "--dry-run",
            "--gh-executable",
            str(gh_symlink),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "GitHub CLI executable must not be a symbolic link" in result.stderr


def test_helper_rejects_github_cli_under_a_writable_parent(tmp_path: Path) -> None:
    writable_bin = tmp_path / "writable-bin"
    writable_bin.mkdir()
    writable_bin.chmod(0o777)
    candidate = writable_bin / "gh"
    _write_fake_gh(candidate)

    result = subprocess.run(
        [
            str(HELPER),
            "--dry-run",
            "--gh-executable",
            str(candidate),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "GitHub CLI path has untrusted permissions" in result.stderr


def test_helper_rejects_an_alternate_github_host_before_authentication(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trusted_gh = fake_bin / "gh"
    _write_fake_gh(trusted_gh)
    state = tmp_path / "state"
    state.mkdir()

    environment = os.environ.copy()
    environment["GH_HOST"] = "attacker.example"
    environment["FAKE_GH_STATE"] = str(state)
    result = subprocess.run(
        [
            str(HELPER),
            "--dry-run",
            "--gh-executable",
            str(trusted_gh),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "GH_HOST must be unset or exactly github.com" in result.stderr
    assert not (state / "gh-calls").exists()


def test_helper_rejects_an_unapproved_authenticated_account(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trusted_gh = fake_bin / "gh"
    _write_fake_gh(trusted_gh)
    state = tmp_path / "state"
    state.mkdir()

    environment = os.environ.copy()
    environment["FAKE_GH_STATE"] = str(state)
    environment["FAKE_GH_ACCOUNT"] = "attacker"
    result = subprocess.run(
        [
            str(HELPER),
            "--dry-run",
            "--gh-executable",
            str(trusted_gh),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Authenticated GitHub account is attacker; expected sodejm" in result.stderr


def test_helper_rejects_a_mismatched_repository_identity(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trusted_gh = fake_bin / "gh"
    _write_fake_gh(trusted_gh)
    state = tmp_path / "state"
    state.mkdir()

    environment = os.environ.copy()
    environment["FAKE_GH_STATE"] = str(state)
    environment["FAKE_GH_REPOSITORY"] = "attacker/AncestryLLM"
    result = subprocess.run(
        [
            str(HELPER),
            "--dry-run",
            "--gh-executable",
            str(trusted_gh),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert (
        "GitHub reported repository attacker/AncestryLLM; expected sodejm/AncestryLLM"
        in result.stderr
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS SDK")
def test_apple_identity_exporter_typechecks(tmp_path: Path) -> None:
    exporter = REPOSITORY_ROOT / "scripts" / "export-apple-signing-identity.swift"
    environment = os.environ.copy()
    environment["CLANG_MODULE_CACHE_PATH"] = str(tmp_path / "module-cache")
    environment["SWIFT_MODULE_CACHE_PATH"] = str(tmp_path / "module-cache")

    result = subprocess.run(
        ["xcrun", "swiftc", "-typecheck", str(exporter)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
