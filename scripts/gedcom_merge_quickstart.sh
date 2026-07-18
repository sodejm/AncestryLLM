#!/usr/bin/env bash
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
  --skip-install    Use python3 directly and skip virtual-environment setup.
  -h, --help        Show this help and exit.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
FIXTURE_DIR="${REPO_ROOT}/tests/fixtures/gedcom_merge"
MERGE_TOOL="${REPO_ROOT}/src/ancestryllm/gedcom/engine.py"
SOURCE_A="${FIXTURE_DIR}/quality-source-a.ged"
SOURCE_B="${FIXTURE_DIR}/quality-source-b.ged"
MALFORMED_SOURCE="${FIXTURE_DIR}/malformed-rejected.ged"
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
[[ -f "$MERGE_TOOL" ]] \
  || fail "packaged merge engine not found"
[[ -d "$FIXTURE_DIR" ]] || fail "fixture directory not found: $FIXTURE_DIR"
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

PYTHON=python3
if [[ "$SKIP_INSTALL" == false ]]; then
  VENV_DIR="${REPO_ROOT}/.venv"
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    printf 'Creating reusable Python environment: %s\n' "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
  fi
  PYTHON="${VENV_DIR}/bin/python"
  printf 'Installing AncestryLLM into %s...\n' "$VENV_DIR"
  "$PYTHON" -m pip install --editable "$REPO_ROOT"
fi

MASTER_GEDCOM="${RUN_DIR}/maren-hollow.ged"
MASTER_REPORT="${RUN_DIR}/maren-hollow.quality.md"
printf 'Merging fixtures with AI disabled and root Maren Hollow...\n'
"$PYTHON" "$MERGE_TOOL" \
  "$SOURCE_A" "$SOURCE_B" \
  --ai-backend none \
  --auto \
  --root-person "Maren Hollow" \
  --output "$MASTER_GEDCOM"

[[ -s "$MASTER_GEDCOM" ]] || fail "expected GEDCOM was not written"
[[ -s "$MASTER_REPORT" ]] || fail "expected quality report was not written"

MALFORMED_GEDCOM="${RUN_DIR}/malformed.ged"
MALFORMED_REPORT="${RUN_DIR}/malformed.quality.md"
printf 'Confirming malformed input fails safely...\n'
set +e
"$PYTHON" "$MERGE_TOOL" \
  "$SOURCE_A" "$MALFORMED_SOURCE" \
  --ai-backend none \
  --auto \
  --quality-root-person "Maren Hollow" \
  --output "$MALFORMED_GEDCOM"
malformed_status=$?
set -e

[[ $malformed_status -ne 0 ]] \
  || fail "malformed input unexpectedly returned success"
[[ ! -e "$MALFORMED_GEDCOM" ]] \
  || fail "malformed input unexpectedly wrote a GEDCOM"
[[ -s "$MALFORMED_REPORT" ]] \
  || fail "malformed input did not write its diagnostic quality report"

printf '\nGEDCOM merge demo passed.\n'
printf '  Rooted GEDCOM: %s\n' "$MASTER_GEDCOM"
printf '  Quality report: %s\n' "$MASTER_REPORT"
printf '  Expected-failure diagnostic: %s\n' "$MALFORMED_REPORT"
