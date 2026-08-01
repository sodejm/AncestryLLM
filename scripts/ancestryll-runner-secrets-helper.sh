#!/bin/bash

# Prepare, validate, and upload the exact AncestryLLM desktop-signing values.
#
# On macOS the default Apple certificate source is one valid Developer ID
# Application identity in the current user's keychain. The helper exports only
# that selected identity, creates a strong one-time PKCS#12 password, and
# derives the Apple Team ID from the certificate name. A user-supplied PKCS#12
# file remains available as an explicit fallback.

set -euo pipefail
set +x
umask 077

readonly GITHUB_HOST='github.com'
readonly APPROVED_GITHUB_ACCOUNT='sodejm'
readonly REPOSITORY='sodejm/AncestryLLM'
readonly SIGNING_ENVIRONMENT='desktop-signing'
readonly MINIMAL_COMMAND_PATH='/usr/bin:/bin:/usr/sbin:/sbin'
readonly UPLOAD_CONFIRMATION="UPLOAD $GITHUB_HOST/$REPOSITORY $SIGNING_ENVIRONMENT AS $APPROVED_GITHUB_ACCOUNT"

MODE='dry-run'
APPLE_CERTIFICATE_MODE='keychain'
APPLE_CERTIFICATE_SOURCE_ARGUMENT=''
GH_EXECUTABLE_ARGUMENT=''
GH_EXECUTABLE=''
TEMP_DIRECTORY=''
REPOSITORY_ROOT=''
APPLE_IDENTITY_EXPORTER=''
CREDENTIAL_SNAPSHOT_HELPER=''

usage() {
  printf '%s\n' \
    'Usage:' \
    '  scripts/ancestryll-runner-secrets-helper.sh [--dry-run|--upload]' \
    '    [--apple-certificate-file PATH] [--gh-executable ABSOLUTE_PATH]' \
    '' \
    'Apple certificate source:' \
    '  By default, discover exactly one valid Developer ID Application identity' \
    '  in the current macOS keychain, export only that identity, generate its' \
    '  PKCS#12 password, and derive APPLE_TEAM_ID.' \
    '  --apple-certificate-file PATH' \
    '             Use an existing PKCS#12 file instead. Its password and Team ID' \
    '             are collected interactively.' \
    '  --gh-executable ABSOLUTE_PATH' \
    '             Use this reviewed, canonical GitHub CLI executable. The path' \
    '             itself must not be a symbolic link.' \
    '' \
    'Modes:' \
    '  --dry-run  Validate tools, authentication, inputs, and generated data.' \
    '             This is the default and performs no uploads.' \
    '  --upload   Validate everything, request explicit confirmation, upload,' \
    '             and verify that all expected names exist.'
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

cleanup() {
  local exit_status=$?

  APPLE_CERTIFICATE_PASSWORD=''
  APPLE_API_KEY_ID=''
  APPLE_API_ISSUER=''
  WINDOWS_CERTIFICATE_PASSWORD=''
  LINUX_GPG_PASSPHRASE=''
  APPLE_CERTIFICATE_SOURCE_ARGUMENT=''
  APPLE_IDENTITY_NAME=''

  if [ -n "$TEMP_DIRECTORY" ] && [ -d "$TEMP_DIRECTORY" ]; then
    case "$TEMP_DIRECTORY" in
      "${TMPDIR:-/tmp}"/ancestryllm-runner-secrets.*)
        chmod -R u+rwX "$TEMP_DIRECTORY" 2>/dev/null || true
        rm -rf -- "$TEMP_DIRECTORY"
        ;;
      *)
        printf 'WARNING: Refusing to remove unexpected temporary path: %s\n' \
          "$TEMP_DIRECTORY" >&2
        ;;
    esac
  fi

  exit "$exit_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      MODE='dry-run'
      ;;
    --upload)
      MODE='upload'
      ;;
    --apple-certificate-file)
      shift
      [ "$#" -gt 0 ] || fail '--apple-certificate-file requires a path'
      APPLE_CERTIFICATE_MODE='file'
      APPLE_CERTIFICATE_SOURCE_ARGUMENT=$1
      ;;
    --gh-executable)
      shift
      [ "$#" -gt 0 ] || fail '--gh-executable requires an absolute path'
      GH_EXECUTABLE_ARGUMENT=$1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "Unknown argument: $1"
      ;;
  esac
  shift
done

case "${GH_HOST-}" in
  ''|"$GITHUB_HOST") ;;
  *) fail "GH_HOST must be unset or exactly $GITHUB_HOST" ;;
esac

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command is missing: $1"
}

path_owner_and_mode() {
  local inspected_path=$1

  case "$(/usr/bin/uname -s)" in
    Darwin)
      /usr/bin/stat -f '%u %Lp' -- "$inspected_path"
      ;;
    Linux)
      /usr/bin/stat -c '%u %a' -- "$inspected_path"
      ;;
    *)
      fail 'Trusted GitHub CLI validation supports only macOS and Linux'
      ;;
  esac
}

validate_trusted_gh_path() {
  local inspected_path=$GH_EXECUTABLE
  local current_uid=''
  local metadata=''
  local owner_uid=''
  local permission_mode=''

  current_uid=$(/usr/bin/id -u) \
    || fail 'Could not determine the current user while validating GitHub CLI'

  while :; do
    metadata=$(path_owner_and_mode "$inspected_path") \
      || fail "Could not inspect GitHub CLI path component: $inspected_path"
    owner_uid=${metadata%% *}
    permission_mode=${metadata#* }

    if [ "$owner_uid" != '0' ] && [ "$owner_uid" != "$current_uid" ]; then
      fail "GitHub CLI path has an untrusted owner: $inspected_path"
    fi
    if (( (8#$permission_mode & 8#022) != 0 )); then
      fail "GitHub CLI path has untrusted permissions: $inspected_path"
    fi

    [ "$inspected_path" != '/' ] || break
    inspected_path=$(/usr/bin/dirname -- "$inspected_path") \
      || fail "Could not inspect GitHub CLI parent path: $inspected_path"
  done
}

resolve_trusted_gh() {
  local candidate=''
  local canonical_candidate=''

  if [ -n "$GH_EXECUTABLE_ARGUMENT" ]; then
    case "$GH_EXECUTABLE_ARGUMENT" in
      /*) ;;
      *) fail '--gh-executable requires an absolute path' ;;
    esac
    [ ! -L "$GH_EXECUTABLE_ARGUMENT" ] \
      || fail 'GitHub CLI executable must not be a symbolic link'
    canonical_candidate=$(/bin/realpath -- "$GH_EXECUTABLE_ARGUMENT") \
      || fail "Could not resolve GitHub CLI executable: $GH_EXECUTABLE_ARGUMENT"
    [ "$canonical_candidate" = "$GH_EXECUTABLE_ARGUMENT" ] \
      || fail 'GitHub CLI executable must be a canonical path without symbolic links'
  else
    for candidate in /opt/homebrew/bin/gh /usr/local/bin/gh /usr/bin/gh; do
      if [ -x "$candidate" ]; then
        canonical_candidate=$(/bin/realpath -- "$candidate") \
          || fail "Could not resolve GitHub CLI executable: $candidate"
        break
      fi
    done
    [ -n "$canonical_candidate" ] \
      || fail 'GitHub CLI was not found in a trusted default location; supply --gh-executable with its reviewed canonical path'
  fi

  [ -f "$canonical_candidate" ] \
    || fail "GitHub CLI executable is not a regular file: $canonical_candidate"
  [ -x "$canonical_candidate" ] \
    || fail "GitHub CLI executable is not executable: $canonical_candidate"

  GH_EXECUTABLE=$canonical_candidate
  validate_trusted_gh_path
}

run_gh() {
  GH_HOST=$GITHUB_HOST PATH=$MINIMAL_COMMAND_PATH "$GH_EXECUTABLE" "$@"
}

snapshot_credential_source_file() {
  local description=$1
  local supplied_path=$2
  local snapshot_name=$3
  local snapshot_path="$TEMP_DIRECTORY/$snapshot_name"

  [ -n "$supplied_path" ] || {
    printf 'ERROR: %s requires a path\n' "$description" >&2
    return 1
  }
  /usr/bin/python3 "$CREDENTIAL_SNAPSHOT_HELPER" \
    --source "$supplied_path" \
    --destination "$snapshot_path" \
    --repository-root "$REPOSITORY_ROOT" \
    || return 1

  REPLY=$snapshot_path
}

prompt_readable_file() {
  local description=$1
  local snapshot_name=$2
  local supplied_path=''

  while :; do
    printf '%s: ' "$description" >&2
    IFS= read -r supplied_path || fail "Input ended while reading $description"

    if [ -z "$supplied_path" ]; then
      printf 'A path is required.\n' >&2
      continue
    fi
    if snapshot_credential_source_file \
      "$description" "$supplied_path" "$snapshot_name"
    then
      return 0
    fi
    printf 'The credential source was rejected; try again.\n' >&2
  done
}

prompt_secret_twice() {
  local description=$1
  local destination_variable=$2
  local first_value=''
  local second_value=''

  while :; do
    printf '%s: ' "$description" >&2
    IFS= read -r -s first_value || fail "Input ended while reading $description"
    printf '\nConfirm %s: ' "$description" >&2
    IFS= read -r -s second_value || fail "Input ended while confirming $description"
    printf '\n' >&2

    [ -n "$first_value" ] || {
      printf 'The value cannot be empty.\n' >&2
      continue
    }
    [ "$first_value" = "$second_value" ] || {
      printf 'The values did not match; try again.\n' >&2
      first_value=''
      second_value=''
      continue
    }

    printf -v "$destination_variable" '%s' "$first_value"
    first_value=''
    second_value=''
    return 0
  done
}

prompt_public_value() {
  local description=$1
  local destination_variable=$2
  local supplied_value=''

  printf '%s: ' "$description" >&2
  IFS= read -r supplied_value || fail "Input ended while reading $description"
  [ -n "$supplied_value" ] || fail "$description cannot be empty"
  printf -v "$destination_variable" '%s' "$supplied_value"
}

discover_apple_developer_id_identity() {
  local identity_output=''
  local identity_pattern='^[[:space:]]*[0-9]+\)[[:space:]]+[0-9A-Fa-f]{40}[[:space:]]+"(Developer ID Application: .+ \(([A-Z0-9]{10})\))"$'
  local identity_count=0
  local line=''

  identity_output=$(security find-identity -v -p codesigning 2>/dev/null) \
    || fail 'Could not inspect code-signing identities in the macOS keychain'

  while IFS= read -r line; do
    if [[ $line =~ $identity_pattern ]]; then
      identity_count=$((identity_count + 1))
      APPLE_IDENTITY_NAME=${BASH_REMATCH[1]}
      APPLE_TEAM_ID=${BASH_REMATCH[2]}
    fi
  done <<< "$identity_output"
  identity_output=''

  case "$identity_count" in
    0)
      fail 'No valid Developer ID Application identity was found in the macOS keychain'
      ;;
    1)
      ;;
    *)
      fail 'Multiple valid Developer ID Application identities were found; use --apple-certificate-file with an explicitly exported PKCS#12 file'
      ;;
  esac
}

export_apple_keychain_identity() {
  APPLE_CERTIFICATE_SOURCE_FILE="$TEMP_DIRECTORY/apple-certificate.p12"
  APPLE_CERTIFICATE_PASSWORD=$(openssl rand -base64 48 | tr -d '\r\n') \
    || fail 'Could not generate the Apple PKCS#12 password'
  [ "${#APPLE_CERTIFICATE_PASSWORD}" -ge 32 ] \
    || fail 'Generated Apple PKCS#12 password was unexpectedly short'

  mkdir -m 700 "$TEMP_DIRECTORY/swift-module-cache"
  if ! printf '%s\n' "$APPLE_CERTIFICATE_PASSWORD" \
    | CLANG_MODULE_CACHE_PATH="$TEMP_DIRECTORY/swift-module-cache" \
      SWIFT_MODULE_CACHE_PATH="$TEMP_DIRECTORY/swift-module-cache" \
      xcrun swift "$APPLE_IDENTITY_EXPORTER" \
        --identity-name "$APPLE_IDENTITY_NAME" \
        --output "$APPLE_CERTIFICATE_SOURCE_FILE" >/dev/null
  then
    fail 'Could not export the selected Developer ID Application identity; unlock the keychain, allow private-key access, and retry'
  fi

  [ -s "$APPLE_CERTIFICATE_SOURCE_FILE" ] \
    || fail 'The exported Apple PKCS#12 payload was empty'
  chmod 600 "$APPLE_CERTIFICATE_SOURCE_FILE"
}

encode_and_validate() {
  local source_file=$1
  local encoded_file=$2
  local decoded_file=$3

  /usr/bin/base64 < "$source_file" > "$encoded_file"
  [ -s "$encoded_file" ] || fail "Base64 output is empty for $source_file"

  /usr/bin/base64 -d < "$encoded_file" > "$decoded_file" \
    || fail "Generated Base64 could not be decoded for $source_file"
  cmp -s "$source_file" "$decoded_file" \
    || fail "Base64 round-trip validation failed for $source_file"

  chmod 600 "$encoded_file" "$decoded_file"
}

upload_secret_file() {
  local name=$1
  local path=$2
  run_gh secret set "$name" -R "$REPOSITORY" -e "$SIGNING_ENVIRONMENT" \
    < "$path"
}

upload_secret_value() {
  local name=$1
  local value=$2
  printf '%s' "$value" \
    | run_gh secret set "$name" -R "$REPOSITORY" -e "$SIGNING_ENVIRONMENT"
}

verify_expected_names() {
  local secret_names=''
  local variable_names=''
  local name=''

  secret_names=$(run_gh secret list -R "$REPOSITORY" -e "$SIGNING_ENVIRONMENT" \
    | awk '{print $1}')
  variable_names=$(run_gh variable list -R "$REPOSITORY" | awk '{print $1}')

  for name in \
    APPLE_CERTIFICATE_BASE64 \
    APPLE_CERTIFICATE_PASSWORD \
    APPLE_API_KEY_BASE64 \
    APPLE_API_KEY_ID \
    APPLE_API_ISSUER \
    WINDOWS_CERTIFICATE_BASE64 \
    WINDOWS_CERTIFICATE_PASSWORD \
    LINUX_GPG_PRIVATE_KEY_BASE64 \
    LINUX_GPG_PASSPHRASE
  do
    printf '%s\n' "$secret_names" | grep -Fxq "$name" \
      || fail "Upload verification did not find environment secret: $name"
  done

  for name in \
    APPLE_TEAM_ID \
    WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT \
    LINUX_GPG_SIGNING_FINGERPRINT \
    LINUX_GPG_PUBLIC_KEY_BASE64
  do
    printf '%s\n' "$variable_names" | grep -Fxq "$name" \
      || fail "Upload verification did not find repository variable: $name"
  done
}

verify_public_variable_value() {
  local name=$1
  local expected_value=$2
  local observed_value=''

  observed_value=$(run_gh variable get "$name" -R "$REPOSITORY" \
    --json value --jq '.value') \
    || fail "Could not read repository variable for verification: $name"
  [ "$observed_value" = "$expected_value" ] \
    || fail "Repository variable did not match the uploaded value: $name"
}

require_command awk
require_command chmod
require_command cmp
require_command grep
require_command mkdir
require_command mktemp
require_command rm
require_command tr
require_command uname
require_command /usr/bin/base64
require_command /usr/bin/python3

resolve_trusted_gh

REPOSITORY_ROOT=$(/bin/realpath \
  "$(/usr/bin/dirname "${BASH_SOURCE[0]}")/..") \
  || fail 'Could not resolve the repository root'
APPLE_IDENTITY_EXPORTER="$REPOSITORY_ROOT/scripts/export-apple-signing-identity.swift"
CREDENTIAL_SNAPSHOT_HELPER="$REPOSITORY_ROOT/scripts/snapshot_credential_file.py"
[ -f "$CREDENTIAL_SNAPSHOT_HELPER" ] && [ ! -L "$CREDENTIAL_SNAPSHOT_HELPER" ] \
  || fail 'The credential snapshot helper is missing or is a symbolic link'

GH_VERSION=$(run_gh --version) \
  || fail "Could not execute the trusted GitHub CLI: $GH_EXECUTABLE"
GH_VERSION=$(printf '%s\n' "$GH_VERSION" | /usr/bin/awk 'NR == 1 { print; exit }')
printf '%s\n' \
  "GitHub CLI executable: $GH_EXECUTABLE" \
  "GitHub CLI identity: $GH_VERSION"
GH_VERSION=''

run_gh auth status --hostname "$GITHUB_HOST" >/dev/null 2>&1 \
  || fail 'GitHub CLI is not authenticated. Use the approved authentication method, then retry.'
ACTIVE_GITHUB_ACCOUNT=$(run_gh api --hostname "$GITHUB_HOST" user --jq '.login') \
  || fail "Could not determine the authenticated account on $GITHUB_HOST."
[ "$ACTIVE_GITHUB_ACCOUNT" = "$APPROVED_GITHUB_ACCOUNT" ] \
  || fail "Authenticated GitHub account is $ACTIVE_GITHUB_ACCOUNT; expected $APPROVED_GITHUB_ACCOUNT"
OBSERVED_REPOSITORY=$(run_gh repo view "$REPOSITORY" \
  --json nameWithOwner --jq '.nameWithOwner') \
  || fail "Cannot access repository $REPOSITORY with the current GitHub CLI authorization."
[ "$OBSERVED_REPOSITORY" = "$REPOSITORY" ] \
  || fail "GitHub reported repository $OBSERVED_REPOSITORY; expected $REPOSITORY"

if [ "$APPLE_CERTIFICATE_MODE" = 'keychain' ]; then
  require_command security
  require_command xcrun
  require_command openssl
  [ "$(uname -s)" = 'Darwin' ] \
    || fail 'Automatic Apple identity discovery requires macOS'
  [ -f "$APPLE_IDENTITY_EXPORTER" ] \
    || fail 'The Apple keychain identity exporter is missing from the repository'
fi

TEMP_DIRECTORY=$(mktemp -d "${TMPDIR:-/tmp}/ancestryllm-runner-secrets.XXXXXX") \
  || fail 'Could not create a secure temporary directory'
chmod 700 "$TEMP_DIRECTORY"

APPLE_CERTIFICATE_PASSWORD=''
APPLE_API_KEY_ID=''
APPLE_API_ISSUER=''
WINDOWS_CERTIFICATE_PASSWORD=''
LINUX_GPG_PASSPHRASE=''
APPLE_TEAM_ID=''
APPLE_IDENTITY_NAME=''
WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT=''
LINUX_GPG_SIGNING_FINGERPRINT=''

printf '%s\n' \
  "GitHub host: $GITHUB_HOST" \
  "Authenticated GitHub account: $ACTIVE_GITHUB_ACCOUNT" \
  "Destination repository: $REPOSITORY" \
  "Secret environment: $SIGNING_ENVIRONMENT" \
  "Mode: $MODE" \
  ''

if [ "$APPLE_CERTIFICATE_MODE" = 'keychain' ]; then
  printf '%s\n' \
    'Apple certificate source: current macOS keychain' \
    'Searching for one valid Developer ID Application identity...'
  discover_apple_developer_id_identity
  export_apple_keychain_identity
  printf '%s\n' \
    'Selected and exported exactly one valid Developer ID Application identity.' \
    'Generated its temporary PKCS#12 password and derived APPLE_TEAM_ID.' \
    ''
else
  printf '%s\n' \
    'Apple certificate source: explicit PKCS#12 file' \
    ''
  snapshot_credential_source_file \
    'APPLE_CERTIFICATE_SOURCE_FILE' \
    "$APPLE_CERTIFICATE_SOURCE_ARGUMENT" \
    'apple-certificate.snapshot' \
    || fail 'APPLE_CERTIFICATE_SOURCE_FILE could not be snapshotted securely'
  APPLE_CERTIFICATE_SOURCE_FILE=$REPLY
fi

printf '%s\n' \
  'Supply the remaining original, unencoded payload files. The helper opens' \
  'each source once, immediately creates a private descriptor-bound snapshot,' \
  'then uses only that snapshot for Base64 and byte-comparison validation.' \
  'It does not create Apple notary API keys, Windows certificates, or GPG keys.' \
  ''

prompt_readable_file \
  'Path to [APPLE_API_KEY_SOURCE_FILE]' 'apple-api-key.snapshot'
APPLE_API_KEY_SOURCE_FILE=$REPLY
prompt_readable_file \
  'Path to [WINDOWS_CERTIFICATE_SOURCE_FILE]' 'windows-certificate.snapshot'
WINDOWS_CERTIFICATE_SOURCE_FILE=$REPLY
prompt_readable_file \
  'Path to [LINUX_GPG_PRIVATE_KEY_SOURCE_FILE]' 'linux-private.snapshot'
LINUX_GPG_PRIVATE_KEY_SOURCE_FILE=$REPLY
prompt_readable_file \
  'Path to [LINUX_GPG_PUBLIC_KEY_SOURCE_FILE]' 'linux-public.snapshot'
LINUX_GPG_PUBLIC_KEY_SOURCE_FILE=$REPLY

APPLE_CERTIFICATE_BASE64_FILE="$TEMP_DIRECTORY/apple-certificate.b64"
APPLE_API_KEY_BASE64_FILE="$TEMP_DIRECTORY/apple-api-key.b64"
WINDOWS_CERTIFICATE_BASE64_FILE="$TEMP_DIRECTORY/windows-certificate.b64"
LINUX_GPG_PRIVATE_KEY_BASE64_FILE="$TEMP_DIRECTORY/linux-private.b64"
LINUX_GPG_PUBLIC_KEY_BASE64_FILE="$TEMP_DIRECTORY/linux-public.b64"

encode_and_validate "$APPLE_CERTIFICATE_SOURCE_FILE" \
  "$APPLE_CERTIFICATE_BASE64_FILE" "$TEMP_DIRECTORY/apple-certificate.decoded"
encode_and_validate "$APPLE_API_KEY_SOURCE_FILE" \
  "$APPLE_API_KEY_BASE64_FILE" "$TEMP_DIRECTORY/apple-api-key.decoded"
encode_and_validate "$WINDOWS_CERTIFICATE_SOURCE_FILE" \
  "$WINDOWS_CERTIFICATE_BASE64_FILE" "$TEMP_DIRECTORY/windows-certificate.decoded"
encode_and_validate "$LINUX_GPG_PRIVATE_KEY_SOURCE_FILE" \
  "$LINUX_GPG_PRIVATE_KEY_BASE64_FILE" "$TEMP_DIRECTORY/linux-private.decoded"
encode_and_validate "$LINUX_GPG_PUBLIC_KEY_SOURCE_FILE" \
  "$LINUX_GPG_PUBLIC_KEY_BASE64_FILE" "$TEMP_DIRECTORY/linux-public.decoded"

if [ "$APPLE_CERTIFICATE_MODE" = 'file' ]; then
  prompt_secret_twice 'APPLE_CERTIFICATE_PASSWORD' APPLE_CERTIFICATE_PASSWORD
fi
prompt_secret_twice 'APPLE_API_KEY_ID' APPLE_API_KEY_ID
prompt_secret_twice 'APPLE_API_ISSUER' APPLE_API_ISSUER
prompt_secret_twice 'WINDOWS_CERTIFICATE_PASSWORD' WINDOWS_CERTIFICATE_PASSWORD
prompt_secret_twice 'LINUX_GPG_PASSPHRASE' LINUX_GPG_PASSPHRASE

if [ "$APPLE_CERTIFICATE_MODE" = 'file' ]; then
  prompt_public_value 'APPLE_TEAM_ID (10 uppercase letters or digits)' APPLE_TEAM_ID
fi
case "$APPLE_TEAM_ID" in
  *[!A-Z0-9]*|'')
    fail 'APPLE_TEAM_ID must contain only uppercase letters or digits'
    ;;
esac
[ "${#APPLE_TEAM_ID}" -eq 10 ] \
  || fail 'APPLE_TEAM_ID must contain exactly 10 characters'

prompt_public_value \
  'WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT (40 hexadecimal characters)' \
  WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT
WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT=$(printf '%s' \
  "$WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT" | tr '[:lower:]' '[:upper:]')
case "$WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT" in
  *[!0-9A-F]*|'')
    fail 'WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT must contain only hexadecimal characters'
    ;;
esac
[ "${#WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT}" -eq 40 ] \
  || fail 'WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT must contain exactly 40 characters'

prompt_public_value \
  'LINUX_GPG_SIGNING_FINGERPRINT (complete 40- or 64-character hexadecimal fingerprint)' \
  LINUX_GPG_SIGNING_FINGERPRINT
LINUX_GPG_SIGNING_FINGERPRINT=$(printf '%s' "$LINUX_GPG_SIGNING_FINGERPRINT" \
  | tr '[:lower:]' '[:upper:]')
case "$LINUX_GPG_SIGNING_FINGERPRINT" in
  *[!0-9A-F]*|'')
    fail 'LINUX_GPG_SIGNING_FINGERPRINT must contain only hexadecimal characters'
    ;;
esac
case "${#LINUX_GPG_SIGNING_FINGERPRINT}" in
  40|64) ;;
  *) fail 'LINUX_GPG_SIGNING_FINGERPRINT must contain 40 or 64 characters' ;;
esac

printf '%s\n' '' 'Validated without displaying values:'
if [ "$APPLE_CERTIFICATE_MODE" = 'keychain' ]; then
  printf '%s\n' \
    '  1 selected Apple Developer ID Application keychain identity' \
    '  4 descriptor-bound private source snapshots' \
    '  1 generated Apple PKCS#12 password and 4 twice-confirmed private values'
else
  printf '%s\n' \
    '  5 descriptor-bound private source snapshots' \
    '  5 twice-confirmed private text values'
fi
printf '%s\n' \
  '  5 Base64 encode/decode byte-for-byte round trips' \
  '  APPLE_TEAM_ID syntax and length' \
  '  Windows certificate thumbprint length and hexadecimal syntax' \
  '  Linux signing fingerprint length and hexadecimal syntax'

if [ "$MODE" = 'dry-run' ]; then
  printf '%s\n' \
    '' \
    'DRY RUN COMPLETE: nothing was uploaded.' \
    'Run again with --upload when ready.'
  exit 0
fi

printf '%s\n' \
  '' \
  "The following operation will update 9 environment secrets in $SIGNING_ENVIRONMENT" \
  "and 4 repository variables in $REPOSITORY." \
  'Existing values with these names will be replaced.' \
  "Type $UPLOAD_CONFIRMATION to continue:"
IFS= read -r confirmation || fail 'Input ended before confirmation'
[ "$confirmation" = "$UPLOAD_CONFIRMATION" ] \
  || fail "Upload cancelled; confirmation did not match $UPLOAD_CONFIRMATION"

upload_secret_file APPLE_CERTIFICATE_BASE64 "$APPLE_CERTIFICATE_BASE64_FILE"
upload_secret_value APPLE_CERTIFICATE_PASSWORD "$APPLE_CERTIFICATE_PASSWORD"
upload_secret_file APPLE_API_KEY_BASE64 "$APPLE_API_KEY_BASE64_FILE"
upload_secret_value APPLE_API_KEY_ID "$APPLE_API_KEY_ID"
upload_secret_value APPLE_API_ISSUER "$APPLE_API_ISSUER"
upload_secret_file WINDOWS_CERTIFICATE_BASE64 "$WINDOWS_CERTIFICATE_BASE64_FILE"
upload_secret_value WINDOWS_CERTIFICATE_PASSWORD "$WINDOWS_CERTIFICATE_PASSWORD"
upload_secret_file LINUX_GPG_PRIVATE_KEY_BASE64 "$LINUX_GPG_PRIVATE_KEY_BASE64_FILE"
upload_secret_value LINUX_GPG_PASSPHRASE "$LINUX_GPG_PASSPHRASE"

run_gh variable set APPLE_TEAM_ID -R "$REPOSITORY" --body "$APPLE_TEAM_ID"
run_gh variable set WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT \
  -R "$REPOSITORY" --body "$WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT"
run_gh variable set LINUX_GPG_SIGNING_FINGERPRINT \
  -R "$REPOSITORY" --body "$LINUX_GPG_SIGNING_FINGERPRINT"
run_gh variable set LINUX_GPG_PUBLIC_KEY_BASE64 \
  -R "$REPOSITORY" < "$LINUX_GPG_PUBLIC_KEY_BASE64_FILE"

verify_expected_names
verify_public_variable_value APPLE_TEAM_ID "$APPLE_TEAM_ID"
verify_public_variable_value WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT \
  "$WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT"
verify_public_variable_value LINUX_GPG_SIGNING_FINGERPRINT \
  "$LINUX_GPG_SIGNING_FINGERPRINT"
LINUX_GPG_PUBLIC_KEY_BASE64=$(< "$LINUX_GPG_PUBLIC_KEY_BASE64_FILE")
verify_public_variable_value LINUX_GPG_PUBLIC_KEY_BASE64 \
  "$LINUX_GPG_PUBLIC_KEY_BASE64"
LINUX_GPG_PUBLIC_KEY_BASE64=''
printf '%s\n' \
  '' \
  'UPLOAD COMPLETE: all 9 secret names were found and all 4 public variable' \
  'values matched. GitHub does not permit secret values to be read back;' \
  'successful uploads and the resulting secret names were verified instead.'
