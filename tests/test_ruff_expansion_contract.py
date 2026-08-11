"""Contracts for reviewed Ruff expansion and runtime-safe typing imports."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

from ancestryllm.api.contracts import CapabilityManifest
from ancestryllm.llm.contracts import GenerationRequest

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_SDK_MODULES = ("anthropic", "google.genai", "ollama", "openai")


def _workflow_job(relative_path: str, job_name: str) -> str:
    workflow = (ROOT / relative_path).read_text(encoding="utf-8")
    marker = f"\n  {job_name}:\n"
    assert marker in workflow
    body = workflow.split(marker, maxsplit=1)[1]
    next_job = re.search(r"(?m)^  [a-z][a-z0-9-]*:\n", body)
    return body[: next_job.start()] if next_job else body


def _imported_modules(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if node.module == "google":
        return tuple(f"google.{alias.name}" for alias in node.names)
    return (node.module,) if node.module else ()


def test_typing_import_rules_are_enabled_without_blanket_policy() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ruff = configuration["tool"]["ruff"]
    lint = ruff["lint"]

    assert "target-version" not in ruff
    assert "TC" in lint["select"]
    assert {"ALL", "D", "E501"}.isdisjoint(lint["select"])
    assert "E501" in lint["ignore"]
    assert lint["flake8-type-checking"] == {
        "runtime-evaluated-base-classes": ["pydantic.BaseModel"]
    }


def test_performance_and_modernization_rules_are_enabled() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    selected = set(configuration["tool"]["ruff"]["lint"]["select"])

    assert {"PERF", "C4", "FURB"} <= selected


def test_language_and_correctness_rules_are_enabled() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lint = configuration["tool"]["ruff"]["lint"]
    selected = set(lint["select"])

    assert {"UP", "SIM", "RET", "PTH", "DTZ", "LOG", "ASYNC"} <= selected
    new_families = ("UP", "SIM", "RET", "PTH", "DTZ", "LOG", "ASYNC")
    assert all(
        not rule.startswith(new_families)
        for ignored in lint["per-file-ignores"].values()
        for rule in ignored
    )


def test_language_and_correctness_suppressions_are_narrow_and_reviewed() -> None:
    new_families = ("UP", "SIM", "RET", "PTH", "DTZ", "LOG", "ASYNC")
    actual: Counter[tuple[str, str]] = Counter()
    for directory in ("scripts", "src", "tests"):
        for path in (ROOT / directory).rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            for match in re.finditer(
                r"# noqa: ([A-Z][A-Z0-9]*(?:\s*,\s*[A-Z][A-Z0-9]*)*)",
                path.read_text(encoding="utf-8"),
            ):
                for rule in re.split(r"\s*,\s*", match.group(1)):
                    if rule.startswith(new_families):
                        actual[(relative, rule)] += 1

    assert actual == Counter(
        {
            ("scripts/bootstrap_uv.py", "PTH100"): 2,
            ("scripts/snapshot_credential_file.py", "PTH100"): 1,
            ("src/ancestryllm/api/contracts.py", "UP040"): 1,
            ("src/ancestryllm/application/dto.py", "UP040"): 1,
            ("src/ancestryllm/application/events.py", "UP040"): 1,
            ("src/ancestryllm/application/executor.py", "UP040"): 2,
            ("src/ancestryllm/core/publication.py", "PTH100"): 1,
            ("src/ancestryllm/gedcom/identity.py", "DTZ001"): 1,
            ("src/ancestryllm/gedcom/identity.py", "DTZ007"): 1,
            ("src/ancestryllm/gedcom/sync_publication.py", "PTH100"): 4,
            ("src/ancestryllm/gedcom/sync_publication.py", "PTH208"): 1,
            ("tests/test_verified_uv_bootstrap.py", "DTZ001"): 1,
        }
    )


def test_ci_uses_github_annotations_through_the_canonical_make_gate() -> None:
    for relative_path in (
        ".github/workflows/ci.yml",
        ".github/workflows/release-readiness.yml",
    ):
        quality_job = _workflow_job(relative_path, "quality")
        assert len(re.findall(r"(?m)^\s+make lint\s*$", quality_job)) == 1
        assert len(re.findall(r"(?m)^\s+RUFF_OUTPUT_FORMAT:\s*github\s*$", quality_job)) == 1


def test_provider_sdk_imports_remain_inside_adapter_call_boundaries() -> None:
    providers = ROOT / "src" / "ancestryllm" / "llm" / "providers"
    violations: list[str] = []
    for path in providers.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            imported = _imported_modules(node)
            if not any(
                module == root or module.startswith(f"{root}.")
                for module in imported
                for root in PROVIDER_SDK_MODULES
            ):
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                parent = parents.get(parent)
            if parent is None:
                relative = path.relative_to(ROOT)
                violations.append(f"{relative}:{node.lineno}:{','.join(imported)}")
    assert violations == []


def test_cli_import_benchmark_isolated_process_loads_no_provider_sdk() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "benchmark_cli_import.py"), "--iterations", "3"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)

    assert report["schema_version"] == 1
    assert report["benchmark"] == "ancestryllm.cli.import"
    assert report["iterations"] == 3
    assert 0 < report["minimum_ms"] <= report["median_ms"] <= report["maximum_ms"]
    assert report["provider_modules_loaded"] == []
    assert set(report) == {
        "schema_version",
        "benchmark",
        "python_version",
        "iterations",
        "minimum_ms",
        "median_ms",
        "maximum_ms",
        "provider_modules_loaded",
    }


def test_provider_selection_does_not_eagerly_load_provider_sdks() -> None:
    program = f"""
import json
import sys

from ancestryllm.llm.registry import ProviderRegistry


class Secrets:
    def get(self, _name):
        return "fictional-provider-secret"


ProviderRegistry(Secrets()).create(sys.argv[1])
provider_roots = {PROVIDER_SDK_MODULES!r}
loaded = sorted(
    module_name
    for module_name in sys.modules
    if any(
        module_name == root or module_name.startswith(root + ".")
        for root in provider_roots
    )
)
print(json.dumps(loaded))
"""
    for provider_id in ("none", "ollama", "openai", "anthropic", "gemini", "openrouter"):
        completed = subprocess.run(
            [sys.executable, "-c", program, provider_id],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == [], provider_id


def test_pydantic_contracts_construct_and_round_trip_runtime_annotations() -> None:
    request = GenerationRequest.model_validate(
        {
            "provider_id": "none",
            "model": "offline",
            "module_id": "test",
            "purpose": "runtime-annotation-regression",
            "messages": [{"role": "user", "content": "Fictional request"}],
        }
    )
    manifest = CapabilityManifest.model_validate(
        {
            "modules": (
                {
                    "module_id": "gedcom",
                    "name": "GEDCOM",
                    "summary": "Fictional capability.",
                    "actions": (
                        {
                            "dispatch_key": "gedcom.validate",
                            "name": "validate",
                            "summary": "Validate fictional data.",
                        },
                    ),
                },
            ),
        }
    )

    assert GenerationRequest.model_validate_json(request.model_dump_json()) == request
    assert CapabilityManifest.model_validate_json(manifest.model_dump_json()) == manifest
    assert request.messages[0].content == "Fictional request"
    assert manifest.modules[0].actions[0].dispatch_key == "gedcom.validate"
