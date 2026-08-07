#!/usr/bin/env bash
# Runs the repository's fictional GEDCOM merge fixtures end-to-end offline
# (no AI/API-key access). Writes a timestamped output directory and never
# modifies its inputs. See --help / usage() below for options and outputs.
set -Eeuo pipefail

umask 077

usage() {
  cat <<EOF
Usage: gedcom_merge_quickstart.sh [OPTIONS]

Run the repository GEDCOM merge fixtures without AI or API-key access. The
script creates a timestamped output directory and never modifies an input.
Initial dependency installation may use pip's configured package index.

Options:
  --output-dir DIR  Create the timestamped run directory beneath DIR.
                    Default: ${TMPDIR:-/tmp}
  --skip-install    Use ANCESTRYLLM_PYTHON directly (default: python3) and
                    skip virtual-environment setup.
  -h, --help        Show this help and exit.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
PUBLIC_FIXTURE_DIR="${REPO_ROOT}/tests/fixtures/gedcom_adversarial"
QUALITY_FIXTURE_DIR="${REPO_ROOT}/tests/fixtures/gedcom_merge"
SOURCE_A="${PUBLIC_FIXTURE_DIR}/xref-source-a.ged"
SOURCE_B="${PUBLIC_FIXTURE_DIR}/xref-source-b.ged"
MALFORMED_SOURCE="${QUALITY_FIXTURE_DIR}/malformed-rejected.ged"
OUTPUT_PARENT="${TMPDIR:-/tmp}"
SKIP_INSTALL=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      [[ $# -ge 2 ]] || fail "--output-dir requires a directory"
      OUTPUT_PARENT=$2
      shift 2
      ;;
    --skip-install)
      SKIP_INSTALL=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      [[ $# -eq 0 ]] || fail "unexpected positional arguments: $*"
      ;;
    -*)
      fail "unknown option: $1 (use --help)"
      ;;
    *)
      fail "unexpected positional argument: $1 (use --help)"
      ;;
  esac
done

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' \
  || fail "Python 3.12 or newer is required"
[[ -f "${REPO_ROOT}/src/ancestryllm/__main__.py" ]] \
  || fail "AncestryLLM module entry point not found"
[[ -d "$PUBLIC_FIXTURE_DIR" ]] \
  || fail "fixture directory not found: $PUBLIC_FIXTURE_DIR"
[[ -d "$QUALITY_FIXTURE_DIR" ]] \
  || fail "fixture directory not found: $QUALITY_FIXTURE_DIR"
[[ -f "$SOURCE_A" ]] || fail "fixture not found: $SOURCE_A"
[[ -f "$SOURCE_B" ]] || fail "fixture not found: $SOURCE_B"
[[ -f "$MALFORMED_SOURCE" ]] || fail "fixture not found: $MALFORMED_SOURCE"

if [[ "$OUTPUT_PARENT" != /* ]]; then
  OUTPUT_PARENT="${PWD}/${OUTPUT_PARENT}"
fi
mkdir -p -- "$OUTPUT_PARENT"

timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
RUN_DIR="${OUTPUT_PARENT%/}/gedcom-merge-${timestamp}-$$"
[[ ! -e "$RUN_DIR" ]] || fail "refusing to reuse output path: $RUN_DIR"
mkdir -- "$RUN_DIR"
RUN_CONFIG_DIR="${RUN_DIR}/.ancestryllm/config"
RUN_DATA_DIR="${RUN_DIR}/.ancestryllm/data"

PYTHON="${ANCESTRYLLM_PYTHON:-python3}"
PYTHONPATH_PREFIX="${REPO_ROOT}/src"
if [[ "$SKIP_INSTALL" == false ]]; then
  VENV_DIR="${REPO_ROOT}/.venv"
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    printf 'Creating reusable Python environment: %s\n' "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
  fi
  PYTHON="${VENV_DIR}/bin/python"
  printf 'Installing AncestryLLM into %s...\n' "$VENV_DIR"
  "$PYTHON" -m pip install --editable "$REPO_ROOT"
  PYTHONPATH_PREFIX=
fi

MASTER_GEDCOM="${RUN_DIR}/aster-fiction.ged"
MASTER_REPORT="${RUN_DIR}/aster-fiction.quality.md"
printf 'Merging fixtures with AI disabled and root Aster Fiction...\n'
ANCESTRYLLM_CONFIG_DIR="$RUN_CONFIG_DIR" \
  ANCESTRYLLM_DATA_DIR="$RUN_DATA_DIR" \
  PYTHONPATH="${PYTHONPATH_PREFIX}${PYTHONPATH_PREFIX:+${PYTHONPATH:+:}}${PYTHONPATH:-}" \
  "$PYTHON" -m ancestryllm gedcom merge \
  "$SOURCE_A" "$SOURCE_B" \
  --provider none \
  --root-person "Aster Fiction" \
  --quality-report "$MASTER_REPORT" \
  --output "$MASTER_GEDCOM"

[[ -s "$MASTER_GEDCOM" ]] || fail "expected GEDCOM was not written"
[[ -s "$MASTER_REPORT" ]] || fail "expected quality report was not written"

MALFORMED_GEDCOM="${RUN_DIR}/malformed.ged"
MALFORMED_REPORT="${RUN_DIR}/malformed.quality.md"
printf 'Confirming malformed input fails safely...\n'
set +e
ANCESTRYLLM_CONFIG_DIR="$RUN_CONFIG_DIR" \
  ANCESTRYLLM_DATA_DIR="$RUN_DATA_DIR" \
  PYTHONPATH="${PYTHONPATH_PREFIX}${PYTHONPATH_PREFIX:+${PYTHONPATH:+:}}${PYTHONPATH:-}" \
  "$PYTHON" -m ancestryllm gedcom merge \
  "$SOURCE_A" "$MALFORMED_SOURCE" \
  --provider none \
  --root-person "Aster Fiction" \
  --quality-report "$MALFORMED_REPORT" \
  --output "$MALFORMED_GEDCOM"
malformed_status=$?
set -e

[[ $malformed_status -ne 0 ]] \
  || fail "malformed input unexpectedly returned success"
[[ ! -e "$MALFORMED_GEDCOM" ]] \
  || fail "malformed input unexpectedly wrote a GEDCOM"
[[ ! -e "$MALFORMED_REPORT" ]] \
  || fail "malformed input unexpectedly wrote a quality report"

printf '\nGEDCOM merge demo passed.\n'
printf '  Rooted GEDCOM: %s\n' "$MASTER_GEDCOM"
printf '  Quality report: %s\n' "$MASTER_REPORT"
printf '  Malformed-input check: no output or report published\n'
