PYTHON ?= python3
VENV_DIR ?= .venv
VENV_PYTHON := $(VENV_DIR)/bin/python

.PHONY: help setup console test lint typecheck security sbom package workflow-audit hooks

help:
	@echo "Available targets: setup console test lint typecheck security sbom package workflow-audit hooks"

setup:
	@$(PYTHON) -m venv $(VENV_DIR)
	@$(VENV_PYTHON) -m pip install --upgrade pip uv
	@$(VENV_PYTHON) -m uv sync --active --all-extras --locked

console:
	@$(VENV_PYTHON) -m ancestryllm

test:
	@$(VENV_PYTHON) -m pytest --verbose

lint:
	@$(VENV_DIR)/bin/ruff check src tests scripts
	@$(VENV_DIR)/bin/ruff format --check src tests scripts
	@./scripts/check_repository_safety.sh

typecheck:
	@$(VENV_DIR)/bin/mypy src/ancestryllm

security:
	@$(VENV_DIR)/bin/pip-audit
	@$(VENV_DIR)/bin/uv run --locked --script scripts/run_pinned_semgrep.py src

sbom:
	@$(VENV_DIR)/bin/cyclonedx-py environment --output-file sbom.json $(VENV_PYTHON)

package:
	@$(VENV_PYTHON) scripts/build_release.py --output-dir dist

workflow-audit:
	@$(VENV_DIR)/bin/zizmor --persona=pedantic .github/workflows

hooks:
	@$(VENV_DIR)/bin/pre-commit install
