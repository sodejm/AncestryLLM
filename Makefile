SHELL := /bin/bash

PYTHON ?= python3
VENV_DIR ?= .venv
VENV_PYTHON := $(VENV_DIR)/bin/python
UV_TOOL_DIR := .tools/uv
UV_RECEIPT := .tools/receipts/uv-bootstrap.json
ifeq ($(OS),Windows_NT)
UV_BIN := $(UV_TOOL_DIR)/uv.exe
else
UV_BIN := $(UV_TOOL_DIR)/uv
endif

.PHONY: help verified-uv setup bootstrap console lock lock-check test lint typecheck security pre-push sbom package workflow-audit hooks desktop-install desktop-check desktop-e2e desktop-security code-docs-check

help:
	@echo "Available targets: setup bootstrap console lock lock-check test lint typecheck security pre-push sbom package workflow-audit hooks desktop-install desktop-check desktop-e2e desktop-security code-docs-check"

desktop-install:
	@pnpm --dir desktop install --frozen-lockfile

desktop-check:
	@pnpm --dir desktop lint
	@pnpm --dir desktop typecheck
	@pnpm --dir desktop test
	@pnpm --dir desktop build

desktop-e2e:
	@pnpm --dir desktop test:e2e

desktop-security:
	@pnpm --dir desktop security
	@pnpm --dir desktop test:security

verified-uv:
	@$(PYTHON) scripts/bootstrap_uv.py bootstrap --install-dir $(UV_TOOL_DIR) --receipt $(UV_RECEIPT) >/dev/null

$(VENV_PYTHON):
	@$(PYTHON) -m venv $(VENV_DIR)

setup: verified-uv $(VENV_PYTHON)
	@VIRTUAL_ENV="$(abspath $(VENV_DIR))" $(UV_BIN) sync --active --locked --no-default-groups --all-extras --group lint --group typecheck --group test --group security --group build

bootstrap: hooks
	@$(MAKE) setup

console:
	@$(VENV_PYTHON) -m ancestryllm

lock: verified-uv
	@$(UV_BIN) lock

lock-check: verified-uv
	@$(UV_BIN) lock --check

test: verified-uv $(VENV_PYTHON)
	@VIRTUAL_ENV="$(abspath $(VENV_DIR))" $(UV_BIN) sync --active --locked --no-default-groups --extra all-llm --group test
	@$(VENV_PYTHON) -m pytest --verbose

lint: verified-uv $(VENV_PYTHON)
	@VIRTUAL_ENV="$(abspath $(VENV_DIR))" $(UV_BIN) sync --active --locked --no-default-groups --group lint
	@$(VENV_DIR)/bin/ruff check src tests scripts
	@$(VENV_DIR)/bin/ruff format --check src tests scripts
	@$(VENV_PYTHON) scripts/check_architecture_contracts.py
	@./scripts/check_repository_safety.sh

typecheck: verified-uv $(VENV_PYTHON)
	@VIRTUAL_ENV="$(abspath $(VENV_DIR))" $(UV_BIN) sync --active --locked --no-default-groups --group typecheck
	@$(VENV_DIR)/bin/mypy src/ancestryllm

security: verified-uv $(VENV_PYTHON)
	@VIRTUAL_ENV="$(abspath $(VENV_DIR))" $(UV_BIN) sync --active --locked --no-default-groups --group security
	@$(VENV_DIR)/bin/pip-audit
	@$(UV_BIN) run --locked --script scripts/run_pinned_semgrep.py .

pre-push:
	@$(MAKE) test
	@$(MAKE) lint
	@$(MAKE) typecheck
	@$(MAKE) security

sbom: verified-uv $(VENV_PYTHON)
	@VIRTUAL_ENV="$(abspath $(VENV_DIR))" $(UV_BIN) sync --active --locked --no-default-groups --group security
	@$(VENV_DIR)/bin/cyclonedx-py environment --output-file sbom.json $(VENV_PYTHON)

package: verified-uv $(VENV_PYTHON)
	@VIRTUAL_ENV="$(abspath $(VENV_DIR))" $(UV_BIN) sync --active --locked --no-default-groups --group build
	@$(VENV_PYTHON) scripts/build_release.py --output-dir dist

workflow-audit: verified-uv $(VENV_PYTHON)
	@VIRTUAL_ENV="$(abspath $(VENV_DIR))" $(UV_BIN) sync --active --locked --no-default-groups --group security
	@$(VENV_DIR)/bin/zizmor --persona=pedantic .github/workflows .github/actions

code-docs-check: verified-uv $(VENV_PYTHON)
	@VIRTUAL_ENV="$(abspath $(VENV_DIR))" $(UV_BIN) sync --active --locked --no-default-groups --group lint
	@$(VENV_PYTHON) scripts/check_code_documentation.py

hooks: verified-uv $(VENV_PYTHON)
	@VIRTUAL_ENV="$(abspath $(VENV_DIR))" $(UV_BIN) sync --active --locked --no-default-groups --group lint
	@$(VENV_DIR)/bin/pre-commit install --hook-type pre-commit --hook-type pre-push
