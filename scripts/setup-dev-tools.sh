#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v npm >/dev/null 2>&1; then
  echo "[dev-setup] npm is required to install language servers."
  exit 1
fi

echo "[dev-setup] Installing project language servers"
npm install --no-fund --no-audit

if command -v code >/dev/null 2>&1; then
  echo "[dev-setup] Installing recommended VS Code extensions"
  code --install-extension ms-python.python --force
  code --install-extension ms-python.vscode-pylance --force
  code --install-extension ms-python.debugpy --force
  code --install-extension ms-azuretools.vscode-docker --force
  code --install-extension redhat.vscode-yaml --force
  code --install-extension mads-hartmann.bash-ide-vscode --force
  code --install-extension mtxr.sqltools --force
else
  echo "[dev-setup] VS Code CLI not found; skipping extension install."
fi

echo "[dev-setup] Development tooling is installed."
