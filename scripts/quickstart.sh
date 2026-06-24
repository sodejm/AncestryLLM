#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[quickstart] Running environment diagnostics"
if [[ ! -f .env ]]; then
  echo "[quickstart] Creating .env from .env.example"
  cp .env.example .env
  echo "[quickstart] Update .env with your keys and local storage paths before startup."
fi

./scripts/doctor.sh || {
  echo "[quickstart] Please fix the issues above and re-run quickstart."
  exit 1
}

if [[ ! -d .venv ]]; then
  echo "[quickstart] Creating virtual environment"
  python3 -m venv .venv
fi

echo "[quickstart] Installing Python dependencies"
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "[quickstart] Installing pre-commit hook"
.venv/bin/pip install pre-commit
.venv/bin/pre-commit install

echo "[quickstart] Starting stack"
.venv/bin/python -m tools.bootstrap

echo "[quickstart] Done. Open http://localhost:3000"
