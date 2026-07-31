PYTHON ?= python3
VENV_DIR ?= .venv
VENV_PYTHON := $(VENV_DIR)/bin/python

.PHONY: help setup bootstrap console lock lock-check test lint typecheck security pre-push sbom package workflow-audit hooks

help:
	@echo "Available targets: setup bootstrap console lock lock-check test lint typecheck security pre-push sbom package workflow-audit hooks"

setup:
	@$(PYTHON) -m venv $(VENV_DIR)
	@$(VENV_PYTHON) -m pip install --upgrade pip uv==0.12.0
	@$(VENV_PYTHON) -m uv sync --active --all-extras --locked

bootstrap: setup hooks

console:
	@$(VENV_PYTHON) -m ancestryllm

lock:
	@$(VENV_PYTHON) -m uv lock

lock-check:
	@$(VENV_PYTHON) -m uv lock --check

test:
	@$(VENV_PYTHON) -m pytest --verbose

lint:
	@$(VENV_DIR)/bin/ruff check src tests scripts
	@$(VENV_DIR)/bin/ruff format --check src tests scripts
	@$(VENV_PYTHON) scripts/check_architecture_contracts.py
	@./scripts/check_repository_safety.sh

typecheck:
	@$(VENV_DIR)/bin/mypy src/ancestryllm

security:
	@$(VENV_DIR)/bin/pip-audit
	@$(VENV_DIR)/bin/uv run --locked --script scripts/run_pinned_semgrep.py src

pre-push: test lint typecheck security

sbom:
	@$(VENV_DIR)/bin/cyclonedx-py environment --output-file sbom.json $(VENV_PYTHON)

package:
	@$(VENV_PYTHON) scripts/build_release.py --output-dir dist

workflow-audit:
	@$(VENV_DIR)/bin/zizmor --persona=pedantic .github/workflows

hooks: setup
	@$(VENV_DIR)/bin/pre-commit install --hook-type pre-commit --hook-type pre-push
