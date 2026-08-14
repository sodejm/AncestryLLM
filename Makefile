SHELL := /bin/bash

VENV_DIR ?= .venv
UV_TOOL_DIR := .tools/uv
UV_RECEIPT := .tools/receipts/uv-bootstrap.json
DIST_DIR ?= dist
UV_BUILD_REPORT ?= build/uv-build-evaluation.json
SBOM_OUTPUT ?= sbom.json
export PYTEST_ADDOPTS ?= --cov --cov-report=term-missing
ifeq ($(OS),Windows_NT)
PYTHON ?= python
UV_BIN := $(UV_TOOL_DIR)/uv.exe
VENV_PYTHON := $(VENV_DIR)/Scripts/python.exe
else
PYTHON ?= python3
UV_BIN := $(UV_TOOL_DIR)/uv
VENV_PYTHON := $(VENV_DIR)/bin/python
endif
export UV_PYTHON := $(PYTHON)

.PHONY: help system-python verified-uv setup bootstrap console lock lock-check test lint markdown-check typecheck typecheck-ty dependency-audit security-static security pre-push sbom package evaluate-uv-build container-policy container-compose-config workflow-audit hooks desktop-install desktop-check desktop-e2e desktop-security code-docs-check docs-screenshots docs-screenshots-check docs-terminal-screenshots

help:
	@echo "Available targets: setup bootstrap console lock lock-check test lint markdown-check typecheck typecheck-ty dependency-audit security pre-push sbom package evaluate-uv-build container-policy container-compose-config workflow-audit hooks desktop-install desktop-check desktop-e2e desktop-security code-docs-check docs-screenshots docs-screenshots-check docs-terminal-screenshots"

desktop-install:
	@node desktop/scripts/install-locked.mjs

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

system-python:
	@command -v "$(PYTHON)" >/dev/null 2>&1 || { echo "UVENV_PYTHON_NOT_FOUND: required system Python executable '$(PYTHON)' was not found" >&2; exit 2; }
	@$(PYTHON) scripts/check_system_python.py

verified-uv: system-python
	@$(PYTHON) scripts/bootstrap_uv.py bootstrap --install-dir $(UV_TOOL_DIR) --receipt $(UV_RECEIPT) >/dev/null

setup: verified-uv
	@$(UV_BIN) sync --locked --all-extras --all-groups

bootstrap: setup hooks

console: verified-uv
	@$(UV_BIN) run --locked ancestry

lock: verified-uv
	@$(UV_BIN) lock

lock-check: verified-uv
	@$(UV_BIN) lock --check

test: verified-uv
	@$(UV_BIN) run --locked --group test pytest --verbose

lint: verified-uv
	@$(UV_BIN) run --locked --group lint ruff check src tests scripts
	@$(UV_BIN) run --locked --group lint ruff format --check src tests scripts
	@$(UV_BIN) run --locked --group lint python scripts/check_gfm_markdown.py
	@$(UV_BIN) run --locked --group lint python scripts/check_architecture_contracts.py
	@./scripts/check_repository_safety.sh
	@$(UV_BIN) run --locked --group lint python scripts/check_code_documentation.py

markdown-check: verified-uv
	@$(UV_BIN) run --locked --group lint python scripts/check_gfm_markdown.py

typecheck: verified-uv
	@$(UV_BIN) run --locked --group typecheck mypy src/ancestryllm

typecheck-ty: verified-uv
	@$(UV_BIN) run --locked --group typecheck ty check src/ancestryllm

dependency-audit: verified-uv
	@$(UV_BIN) run --locked --group security python scripts/run_dependency_audit.py --uv $(UV_BIN)

security-static: verified-uv
	@$(UV_BIN) run --locked --script scripts/run_pinned_semgrep.py .

security: dependency-audit security-static

pre-push:
	@$(MAKE) test
	@$(MAKE) lint
	@$(MAKE) typecheck
	@$(MAKE) security

sbom: verified-uv
	@$(UV_BIN) run --locked --group security cyclonedx-py environment --output-file $(SBOM_OUTPUT) $(VENV_PYTHON)

package: verified-uv
	@$(UV_BIN) run --locked --group build python scripts/build_release.py --output-dir $(DIST_DIR)

evaluate-uv-build: verified-uv
	@$(UV_BIN) run --locked --group build python scripts/evaluate_uv_build.py --uv $(UV_BIN) --report $(UV_BUILD_REPORT)

container-policy: system-python
	@mkdir -p build/container-policy
	@$(PYTHON) scripts/container_policy.py --base containers/compose.yaml --overlay containers/compose.local.yaml --dockerfile containers/Dockerfile --output build/container-policy/local.json
	@$(PYTHON) scripts/container_policy.py --base containers/compose.yaml --overlay containers/compose.remote.yaml --dockerfile containers/Dockerfile --output build/container-policy/remote.json

container-compose-config: container-policy
	@gateway_image="$${ANCESTRYLLM_GATEWAY_IMAGE:?Set ANCESTRYLLM_GATEWAY_IMAGE}"; worker_image="$${ANCESTRYLLM_WORKER_IMAGE:?Set ANCESTRYLLM_WORKER_IMAGE}"; platform="$${ANCESTRYLLM_PLATFORM:?Set ANCESTRYLLM_PLATFORM}"; \
		$(PYTHON) scripts/container_policy.py --base containers/compose.yaml --overlay containers/compose.local.yaml --dockerfile containers/Dockerfile --gateway-image "$$gateway_image" --worker-image "$$worker_image" --platform "$$platform" --output build/container-policy/resolved-local.json && \
		docker compose --file containers/compose.yaml --file containers/compose.local.yaml config >/dev/null
	@gateway_image="$${ANCESTRYLLM_GATEWAY_IMAGE:?Set ANCESTRYLLM_GATEWAY_IMAGE}"; worker_image="$${ANCESTRYLLM_WORKER_IMAGE:?Set ANCESTRYLLM_WORKER_IMAGE}"; platform="$${ANCESTRYLLM_PLATFORM:?Set ANCESTRYLLM_PLATFORM}"; \
		$(PYTHON) scripts/container_policy.py --base containers/compose.yaml --overlay containers/compose.remote.yaml --dockerfile containers/Dockerfile --gateway-image "$$gateway_image" --worker-image "$$worker_image" --platform "$$platform" --output build/container-policy/resolved-remote.json && \
		docker compose --file containers/compose.yaml --file containers/compose.remote.yaml config >/dev/null

workflow-audit: verified-uv
	@$(UV_BIN) run --locked --group security zizmor --persona=pedantic .github/workflows .github/actions

code-docs-check: verified-uv
	@$(UV_BIN) run --locked --group lint python scripts/check_code_documentation.py

docs-screenshots: verified-uv
	@$(UV_BIN) run --locked python scripts/docs_screenshots.py capture --manifest config/docs-screenshot-manifest.json --repository-root .

docs-screenshots-check: verified-uv
	@$(UV_BIN) run --locked python scripts/docs_screenshots.py check --manifest config/docs-screenshot-manifest.json --repository-root .

docs-terminal-screenshots: verified-uv
	@$(UV_BIN) run --locked python scripts/docs_screenshots.py capture --manifest config/docs-screenshot-manifest.json --repository-root . --surface terminal

hooks: verified-uv
	@$(UV_BIN) run --locked --group lint pre-commit install --hook-type pre-commit --hook-type pre-push
