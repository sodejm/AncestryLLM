#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

AUTO_INSTALL="${AUTO_INSTALL:-0}"
AUTO_TROUBLESHOOT="${AUTO_TROUBLESHOOT:-1}"
GENERATED_ADMIN_PASSWORD=0
BOOTSTRAP_ADMIN_EMAIL="admin@localhost"
BOOTSTRAP_ADMIN_PASSWORD=""
if [[ "${1:-}" == "--auto-install" ]]; then
  AUTO_INSTALL=1
  shift
fi

if [[ "$#" -ne 0 ]]; then
  echo "Usage: ./scripts/quickstart.sh [--auto-install]"
  exit 1
fi

if [[ "$AUTO_INSTALL" == "1" ]]; then
  echo "[quickstart] Auto-install mode enabled"
  python3 -m tools.system_setup --auto-install
fi

echo "[quickstart] Running environment diagnostics"
if [[ ! -f .env ]]; then
  echo "[quickstart] Creating .env from .env.example"
  cp .env.example .env
  echo "[quickstart] Update .env with your keys and local storage paths before startup."
fi

if grep -q '^OPEN_WEBUI_DATA_DIR=open-webui-data$' .env 2>/dev/null; then
  echo "[quickstart] Updating OPEN_WEBUI_DATA_DIR to ./open-webui-data for Compose compatibility"
  perl -i -pe 's/^OPEN_WEBUI_DATA_DIR=open-webui-data$/OPEN_WEBUI_DATA_DIR=.\/open-webui-data/' .env
fi

if ! grep -q '^WEBUI_SECRET_KEY=' .env 2>/dev/null || grep -q '^WEBUI_SECRET_KEY=$' .env 2>/dev/null; then
  webui_secret_key="$(openssl rand -hex 32)"
  if grep -q '^WEBUI_SECRET_KEY=' .env 2>/dev/null; then
    perl -i -pe "s/^WEBUI_SECRET_KEY=.*/WEBUI_SECRET_KEY=${webui_secret_key}/" .env
  else
    echo "WEBUI_SECRET_KEY=${webui_secret_key}" >> .env
  fi
  echo "[quickstart] Generated WEBUI_SECRET_KEY for read-only Open WebUI startup"
fi

if ! grep -q '^WEBUI_ADMIN_EMAIL=' .env 2>/dev/null || grep -q '^WEBUI_ADMIN_EMAIL=$' .env 2>/dev/null; then
  if grep -q '^WEBUI_ADMIN_EMAIL=' .env 2>/dev/null; then
    perl -i -pe 's/^WEBUI_ADMIN_EMAIL=.*/WEBUI_ADMIN_EMAIL=admin@localhost/' .env
  else
    echo "WEBUI_ADMIN_EMAIL=admin@localhost" >> .env
  fi
  echo "[quickstart] Set default Open WebUI admin email to admin@localhost"
fi

BOOTSTRAP_ADMIN_EMAIL="$(grep '^WEBUI_ADMIN_EMAIL=' .env | cut -d '=' -f2-)"

if ! grep -q '^WEBUI_ADMIN_PASSWORD=' .env 2>/dev/null || grep -q '^WEBUI_ADMIN_PASSWORD=$' .env 2>/dev/null; then
  BOOTSTRAP_ADMIN_PASSWORD="$(openssl rand -base64 24 | tr -d '\n')"
  if grep -q '^WEBUI_ADMIN_PASSWORD=' .env 2>/dev/null; then
    perl -i -pe "s/^WEBUI_ADMIN_PASSWORD=.*/WEBUI_ADMIN_PASSWORD=${BOOTSTRAP_ADMIN_PASSWORD}/" .env
  else
    echo "WEBUI_ADMIN_PASSWORD=${BOOTSTRAP_ADMIN_PASSWORD}" >> .env
  fi
  GENERATED_ADMIN_PASSWORD=1
  echo "[quickstart] Generated first-run Open WebUI admin password"
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
if ! .venv/bin/python -m tools.bootstrap; then
  if [[ "$AUTO_TROUBLESHOOT" != "1" ]]; then
    echo "[quickstart] Bootstrap failed and auto-troubleshoot is disabled."
    exit 1
  fi

  echo "[quickstart] Bootstrap failed. Attempting auto-troubleshoot..."
  .venv/bin/python -m tools.system_setup --auto-install
  .venv/bin/python -m tools.bootstrap
fi

echo "[quickstart] Done. Open http://localhost:3000"
echo "[quickstart] Default admin account: ${BOOTSTRAP_ADMIN_EMAIL}"
if [[ "$GENERATED_ADMIN_PASSWORD" == "1" ]]; then
  echo "[quickstart] Generated admin password: ${BOOTSTRAP_ADMIN_PASSWORD}"
fi
echo "[quickstart] IMPORTANT: Change the admin password to a unique, stronger value after first login."
