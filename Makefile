PYTHON ?= python3
VENV_DIR ?= .venv
VENV_PYTHON := $(VENV_DIR)/bin/python
VENV_PIP := $(VENV_DIR)/bin/pip

.PHONY: help quickstart quickstart-hosted quickstart-auto-install doctor setup start stop test lint security hooks dev-tools dev-setup

help:
	@echo "Available targets:"
	@echo "  quickstart  - End-to-end local setup + startup"
	@echo "  quickstart-hosted - Same as quickstart but exposes WebUI on all interfaces"
	@echo "  quickstart-auto-install - Quickstart + install missing system deps/services"
	@echo "  doctor      - Validate required local dependencies"
	@echo "  setup       - Create venv and install dependencies"
	@echo "  dev-tools   - Install language servers and editor tooling"
	@echo "  dev-setup   - setup + hooks + dev-tools"
	@echo "  hooks       - Install pre-commit hooks"
	@echo "  start       - Start services via bootstrapper"
	@echo "  stop        - Stop docker compose services"
	@echo "  test        - Run pytest"
	@echo "  lint        - Run pre-commit quality checks"
	@echo "  security    - Run semgrep, pip-audit, trivy, and gitleaks"

quickstart:
	@./scripts/quickstart.sh $(if $(AUTO_INSTALL),--auto-install,)

quickstart-hosted:
	@DEPLOYMENT_MODE=hosted ./scripts/quickstart.sh

quickstart-auto-install:
	@./scripts/quickstart.sh --auto-install

doctor:
	@./scripts/doctor.sh

setup:
	@$(PYTHON) -m venv $(VENV_DIR)
	@$(VENV_PYTHON) -m pip install --upgrade pip
	@$(VENV_PIP) install -r requirements.txt

dev-tools:
	@./scripts/setup-dev-tools.sh

dev-setup: setup hooks dev-tools

hooks:
	@$(VENV_PIP) install pre-commit
	@$(VENV_DIR)/bin/pre-commit install

start:
	@$(VENV_PYTHON) -m tools.bootstrap

stop:
	@docker compose down

test:
	@$(VENV_PYTHON) -m pytest --verbose

lint:
	@$(VENV_DIR)/bin/pre-commit run --all-files

security:
	@semgrep scan --config auto
	@$(VENV_DIR)/bin/pip-audit -r requirements.txt
	@trivy config --skip-dirs node_modules .
	@gitleaks detect --verbose
