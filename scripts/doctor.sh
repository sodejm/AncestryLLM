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

read_dotenv_value() {
  local key="$1"
  local line=""
  local value=""
  local first_char=""
  local last_char=""

  [[ -f .env ]] || return 0

  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^[[:space:]]*${key}=(.*)$ ]]; then
      value="${BASH_REMATCH[1]%$'\r'}"
      if [[ "${#value}" -ge 2 ]]; then
        first_char="${value:0:1}"
        last_char="${value:${#value}-1:1}"
        if [[ "$first_char" == '"' && "$last_char" == '"' ]]; then
          value="${value:1:${#value}-2}"
        elif [[ "$first_char" == "'" && "$last_char" == "'" ]]; then
          value="${value:1:${#value}-2}"
        fi
      fi
      printf '%s\n' "$value"
      return 0
    fi
  done < .env
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

family_trees_dir="$(read_dotenv_value FAMILY_TREES_HOST_DIR)"
family_trees_dir="${family_trees_dir:-./family_trees}"

if [[ ! -d "$family_trees_dir" ]]; then
  echo "[missing] RootsMagic directory: $family_trees_dir"
  missing=1
elif ! find "$family_trees_dir" -maxdepth 1 -type f -name '*.rmtree' | grep -q .; then
  echo "[info] No .rmtree files found in $family_trees_dir yet."
fi

if [[ "$missing" -ne 0 ]]; then
  echo "Environment checks failed. Install missing dependencies and re-run."
  exit 1
fi

echo "Environment checks passed."
