#!/usr/bin/env bash
set -euo pipefail

missing=0

check_cmd() {
  local cmd="$1"
  local label="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "[ok] ${label}: $(command -v "$cmd")"
  else
    echo "[missing] ${label} (${cmd})"
    missing=1
  fi
}

echo "Running local environment checks..."
check_cmd python3 "Python 3"
check_cmd docker "Docker CLI"
check_cmd git "Git"

if [[ "$(uname -s)" == "Darwin" ]]; then
  check_cmd brew "Homebrew (macOS)"
fi

if [[ ! -f .env ]]; then
  echo "[info] .env not found. Copy .env.example to .env before startup."
fi

if [[ ! -d family_trees ]]; then
  echo "[missing] family_trees/ directory"
  missing=1
elif ! find family_trees -maxdepth 1 -type f -name '*.rmtree' | grep -q .; then
  echo "[info] No .rmtree files found in family_trees/ yet."
fi

if [[ "$missing" -ne 0 ]]; then
  echo "Environment checks failed. Install missing dependencies and re-run."
  exit 1
fi

echo "Environment checks passed."
