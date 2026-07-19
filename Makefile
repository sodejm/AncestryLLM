PYTHON ?= python3
VENV_DIR ?= .venv
VENV_PYTHON := $(VENV_DIR)/bin/python

.PHONY: help setup console test lint typecheck security sbom hooks

help:
	@echo "Available targets: setup console test lint typecheck security sbom hooks"

setup:
	@$(PYTHON) -m venv $(VENV_DIR)
	@$(VENV_PYTHON) -m pip install --upgrade pip uv
	@$(VENV_PYTHON) -m uv sync --active --all-extras --locked

console:
	@$(VENV_PYTHON) -m ancestryllm

test:
	@$(VENV_PYTHON) -m pytest --verbose

lint:
	@$(VENV_DIR)/bin/ruff check src tests
	@$(VENV_DIR)/bin/ruff format --check src tests
	@./scripts/check_repository_safety.sh

typecheck:
	@$(VENV_DIR)/bin/mypy src/ancestryllm

security:
	@$(VENV_DIR)/bin/pip-audit
	@uvx semgrep scan --config p/python --config p/secrets src

sbom:
	@$(VENV_DIR)/bin/cyclonedx-py environment --output-file sbom.json $(VENV_PYTHON)

hooks:
	@$(VENV_DIR)/bin/pre-commit install
