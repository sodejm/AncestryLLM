#!/bin/bash

# Prepare, validate, and upload the exact AncestryLLM desktop-signing values.
#
# This helper intentionally does not obtain signing certificates or keys and
# does not provision or register a Windows runner. Those inputs and commands
# are not part of the supplied configuration contract.

set -euo pipefail
set +x
umask 077

readonly REPOSITORY='sodejm/AncestryLLM'
readonly SIGNING_ENVIRONMENT='desktop-signing'

MODE='dry-run'
TEMP_DIRECTORY=''
REPOSITORY_ROOT=''

usage() {
  printf '%s\n' \
    'Usage:' \
    '  scripts/ancestryll-runner-secrets-helper.sh [--dry-run|--upload]' \
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

for argument in "$@"; do
  case "$argument" in
    --dry-run)
      MODE='dry-run'
      ;;
    --upload)
      MODE='upload'
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "Unknown argument: $argument"
      ;;
  esac
done

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command is missing: $1"
}

prompt_readable_file() {
  local description=$1
  local supplied_path=''
  local canonical_path=''

  while :; do
    printf '%s: ' "$description" >&2
    IFS= read -r supplied_path || fail "Input ended while reading $description"

    [ -n "$supplied_path" ] || {
      printf 'A path is required.\n' >&2
      continue
    }
    [ -f "$supplied_path" ] || {
      printf 'Not a regular file: %s\n' "$supplied_path" >&2
      continue
    }
    [ -r "$supplied_path" ] || {
      printf 'File is not readable: %s\n' "$supplied_path" >&2
      continue
    }
    [ -s "$supplied_path" ] || {
      printf 'File is empty: %s\n' "$supplied_path" >&2
      continue
    }

    canonical_path=$(realpath "$supplied_path") \
      || fail "Could not resolve source file path: $supplied_path"
    case "$canonical_path" in
      "$REPOSITORY_ROOT"|"$REPOSITORY_ROOT"/*)
        fail 'Credential source files must be stored outside the repository'
        ;;
    esac

    REPLY=$canonical_path
    return 0
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

encode_and_validate() {
  local source_file=$1
  local encoded_file=$2
  local decoded_file=$3

  /usr/bin/base64 < "$source_file" > "$encoded_file"
  [ -s "$encoded_file" ] || fail "Base64 output is empty for $source_file"

  /usr/bin/base64 -D < "$encoded_file" > "$decoded_file" \
    || fail "Generated Base64 could not be decoded for $source_file"
  cmp -s "$source_file" "$decoded_file" \
    || fail "Base64 round-trip validation failed for $source_file"

  chmod 600 "$encoded_file" "$decoded_file"
}

upload_secret_file() {
  local name=$1
  local path=$2
  gh secret set "$name" -R "$REPOSITORY" -e "$SIGNING_ENVIRONMENT" < "$path"
}

upload_secret_value() {
  local name=$1
  local value=$2
  printf '%s' "$value" \
    | gh secret set "$name" -R "$REPOSITORY" -e "$SIGNING_ENVIRONMENT"
}

verify_expected_names() {
  local secret_names=''
  local variable_names=''
  local name=''

  secret_names=$(gh secret list -R "$REPOSITORY" -e "$SIGNING_ENVIRONMENT" \
    | awk '{print $1}')
  variable_names=$(gh variable list -R "$REPOSITORY" | awk '{print $1}')

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

  observed_value=$(gh variable get "$name" -R "$REPOSITORY" \
    --json value --jq '.value') \
    || fail "Could not read repository variable for verification: $name"
  [ "$observed_value" = "$expected_value" ] \
    || fail "Repository variable did not match the uploaded value: $name"
}

require_command gh
require_command awk
require_command chmod
require_command cmp
require_command grep
require_command mktemp
require_command realpath
require_command rm
require_command tr
require_command /usr/bin/base64

REPOSITORY_ROOT=$(realpath "$(dirname "${BASH_SOURCE[0]}")/..") \
  || fail 'Could not resolve the repository root'

gh auth status >/dev/null 2>&1 \
  || fail 'GitHub CLI is not authenticated. Use the approved authentication method, then retry.'
gh repo view "$REPOSITORY" >/dev/null 2>&1 \
  || fail "Cannot access repository $REPOSITORY with the current GitHub CLI authorization."

TEMP_DIRECTORY=$(mktemp -d "${TMPDIR:-/tmp}/ancestryllm-runner-secrets.XXXXXX") \
  || fail 'Could not create a secure temporary directory'
chmod 700 "$TEMP_DIRECTORY"

printf '%s\n' \
  "Destination repository: $REPOSITORY" \
  "Secret environment: $SIGNING_ENVIRONMENT" \
  "Mode: $MODE" \
  '' \
  'Supply the original, unencoded payload files. This helper creates temporary' \
  'Base64 representations and validates them by decoding and byte-comparing.' \
  'It does not create or retrieve certificates, API keys, or GPG keys.' \
  ''

prompt_readable_file 'Path to [APPLE_CERTIFICATE_SOURCE_FILE]'
APPLE_CERTIFICATE_SOURCE_FILE=$REPLY
prompt_readable_file 'Path to [APPLE_API_KEY_SOURCE_FILE]'
APPLE_API_KEY_SOURCE_FILE=$REPLY
prompt_readable_file 'Path to [WINDOWS_CERTIFICATE_SOURCE_FILE]'
WINDOWS_CERTIFICATE_SOURCE_FILE=$REPLY
prompt_readable_file 'Path to [LINUX_GPG_PRIVATE_KEY_SOURCE_FILE]'
LINUX_GPG_PRIVATE_KEY_SOURCE_FILE=$REPLY
prompt_readable_file 'Path to [LINUX_GPG_PUBLIC_KEY_SOURCE_FILE]'
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

APPLE_CERTIFICATE_PASSWORD=''
APPLE_API_KEY_ID=''
APPLE_API_ISSUER=''
WINDOWS_CERTIFICATE_PASSWORD=''
LINUX_GPG_PASSPHRASE=''
APPLE_TEAM_ID=''
WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT=''
LINUX_GPG_SIGNING_FINGERPRINT=''

prompt_secret_twice 'APPLE_CERTIFICATE_PASSWORD' APPLE_CERTIFICATE_PASSWORD
prompt_secret_twice 'APPLE_API_KEY_ID' APPLE_API_KEY_ID
prompt_secret_twice 'APPLE_API_ISSUER' APPLE_API_ISSUER
prompt_secret_twice 'WINDOWS_CERTIFICATE_PASSWORD' WINDOWS_CERTIFICATE_PASSWORD
prompt_secret_twice 'LINUX_GPG_PASSPHRASE' LINUX_GPG_PASSPHRASE

prompt_public_value 'APPLE_TEAM_ID (10 characters)' APPLE_TEAM_ID
case "$APPLE_TEAM_ID" in
  ??????????) ;;
  *) fail 'APPLE_TEAM_ID must contain exactly 10 characters' ;;
esac

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

printf '%s\n' \
  '' \
  'Validated without displaying values:' \
  '  5 non-empty source payload files' \
  '  5 Base64 encode/decode byte-for-byte round trips' \
  '  5 non-empty, twice-confirmed private text values' \
  '  APPLE_TEAM_ID length' \
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
  'Type UPLOAD to continue:'
IFS= read -r confirmation || fail 'Input ended before confirmation'
[ "$confirmation" = 'UPLOAD' ] || fail 'Upload cancelled; confirmation did not match UPLOAD'

upload_secret_file APPLE_CERTIFICATE_BASE64 "$APPLE_CERTIFICATE_BASE64_FILE"
upload_secret_value APPLE_CERTIFICATE_PASSWORD "$APPLE_CERTIFICATE_PASSWORD"
upload_secret_file APPLE_API_KEY_BASE64 "$APPLE_API_KEY_BASE64_FILE"
upload_secret_value APPLE_API_KEY_ID "$APPLE_API_KEY_ID"
upload_secret_value APPLE_API_ISSUER "$APPLE_API_ISSUER"
upload_secret_file WINDOWS_CERTIFICATE_BASE64 "$WINDOWS_CERTIFICATE_BASE64_FILE"
upload_secret_value WINDOWS_CERTIFICATE_PASSWORD "$WINDOWS_CERTIFICATE_PASSWORD"
upload_secret_file LINUX_GPG_PRIVATE_KEY_BASE64 "$LINUX_GPG_PRIVATE_KEY_BASE64_FILE"
upload_secret_value LINUX_GPG_PASSPHRASE "$LINUX_GPG_PASSPHRASE"

gh variable set APPLE_TEAM_ID -R "$REPOSITORY" --body "$APPLE_TEAM_ID"
gh variable set WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT \
  -R "$REPOSITORY" --body "$WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT"
gh variable set LINUX_GPG_SIGNING_FINGERPRINT \
  -R "$REPOSITORY" --body "$LINUX_GPG_SIGNING_FINGERPRINT"
gh variable set LINUX_GPG_PUBLIC_KEY_BASE64 \
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
