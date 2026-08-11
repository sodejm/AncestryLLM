#!/usr/bin/env python3
"""Capture and compare the executable 0.3.0 core-contracts baseline.

The report intentionally contains only test identifiers, hashes, dependency
names, aggregate counts, and process measurements. Captured test or command
output is never copied into a report because it could contain fixture payloads.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

try:
    import resource
except ImportError:  # pragma: no cover - resource is unavailable on Windows.
    resource = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
MANIFEST_PATH = ROOT / "tests/characterization/core_contracts_0_3_baseline.json"
REPORT_SCHEMA_VERSION = 1


class CharacterizationError(RuntimeError):
    """A payload-safe characterization failure."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CharacterizationError(f"cannot read JSON document: {path.name}") from exc
    if not isinstance(value, dict):
        raise CharacterizationError(f"JSON document must be an object: {path.name}")
    return value


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Load and structurally validate the committed characterization manifest."""

    manifest = _load_json(path)
    if manifest.get("schema_version") != 1:
        raise CharacterizationError("unsupported characterization manifest schema")
    if manifest.get("baseline_id") != "0.3.0-core-contracts":
        raise CharacterizationError("unexpected characterization baseline identifier")

    fixtures = manifest.get("fixture_sha256")
    if not isinstance(fixtures, dict) or not fixtures:
        raise CharacterizationError("fixture hash inventory must be a non-empty object")
    for relative, expected in fixtures.items():
        if not isinstance(relative, str) or not relative.startswith("tests/fixtures/"):
            raise CharacterizationError("fixture inventory contains an invalid path")
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or any(character not in "0123456789abcdef" for character in expected)
        ):
            raise CharacterizationError("fixture inventory contains an invalid SHA-256")

    groups = manifest.get("semantic_groups")
    if not isinstance(groups, list) or not groups:
        raise CharacterizationError("semantic group inventory must be a non-empty list")
    group_ids: set[str] = set()
    test_nodes: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            raise CharacterizationError("semantic group must be an object")
        group_id = group.get("id")
        nodes = group.get("test_nodes")
        if not isinstance(group_id, str) or not group_id or group_id in group_ids:
            raise CharacterizationError("semantic group identifiers must be unique")
        if not isinstance(nodes, list) or not nodes:
            raise CharacterizationError(f"semantic group {group_id} has no tests")
        group_ids.add(group_id)
        for node in nodes:
            if not isinstance(node, str) or "::" not in node or node in test_nodes:
                raise CharacterizationError("test node identifiers must be valid and unique")
            test_nodes.add(node)

    facades = manifest.get("public_facades")
    if (
        not isinstance(facades, list)
        or not facades
        or any(not isinstance(item, str) or not item.startswith("ancestryllm.") for item in facades)
        or len(set(facades)) != len(facades)
    ):
        raise CharacterizationError("public facade inventory must contain unique module names")

    performance = manifest.get("performance")
    required_performance = {
        "runs",
        "warm_iterations_per_run",
        "cold_start_regression_percent",
        "cold_start_regression_floor_ms",
        "warm_regression_percent",
        "warm_regression_minimum_ms",
        "peak_rss_regression_percent",
    }
    if not isinstance(performance, dict) or set(performance) != required_performance:
        raise CharacterizationError("performance policy is incomplete")
    if not isinstance(performance["runs"], int) or performance["runs"] < 7:
        raise CharacterizationError("performance capture requires at least seven runs")
    if (
        not isinstance(performance["warm_iterations_per_run"], int)
        or performance["warm_iterations_per_run"] < 1
    ):
        raise CharacterizationError("warm performance capture requires iterations")
    for key in required_performance - {"runs", "warm_iterations_per_run"}:
        value = performance[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise CharacterizationError(f"performance policy value must be positive: {key}")
    return manifest


def fixture_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    """Return hashes for the exact fixed fictional corpus in the manifest."""

    hashes: dict[str, str] = {}
    for relative in sorted(manifest["fixture_sha256"]):
        path = (ROOT / relative).resolve()
        try:
            path.relative_to((ROOT / "tests/fixtures").resolve())
        except ValueError as exc:
            raise CharacterizationError("fixture inventory escaped tests/fixtures") from exc
        if not path.is_file():
            raise CharacterizationError(f"fixture is missing: {relative}")
        hashes[relative] = _sha256(path)
    return hashes


def _assert_fixture_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    actual = fixture_hashes(manifest)
    if actual != manifest["fixture_sha256"]:
        raise CharacterizationError("fixed fictional fixture corpus does not match its manifest")
    return actual


def _module_file(module_name: str) -> Path | None:
    relative = Path(*module_name.split("."))
    module = SRC_ROOT / relative.with_suffix(".py")
    package = SRC_ROOT / relative / "__init__.py"
    if module.is_file():
        return module
    if package.is_file():
        return package
    return None


def _defined_test_functions(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    except (OSError, SyntaxError) as exc:
        raise CharacterizationError(f"cannot parse test inventory file: {path.name}") from exc
    return {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def verify_inventory(manifest: dict[str, Any]) -> dict[str, int]:
    """Verify fixture hashes, stable pytest nodes, and supported façade modules."""

    _assert_fixture_hashes(manifest)
    cached_functions: dict[Path, set[str]] = {}
    node_count = 0
    for group in manifest["semantic_groups"]:
        for node in group["test_nodes"]:
            relative, function_name, *remainder = node.split("::")
            if (
                remainder
                or not relative.startswith("tests/")
                or not function_name.startswith("test_")
            ):
                raise CharacterizationError(
                    "characterization uses an unsupported pytest node shape"
                )
            test_path = (ROOT / relative).resolve()
            try:
                test_path.relative_to((ROOT / "tests").resolve())
            except ValueError as exc:
                raise CharacterizationError("test inventory escaped tests") from exc
            if not test_path.is_file():
                raise CharacterizationError(f"test inventory file is missing: {relative}")
            functions = cached_functions.setdefault(test_path, _defined_test_functions(test_path))
            if function_name not in functions:
                raise CharacterizationError(f"test inventory function is missing: {node}")
            node_count += 1

    for module_name in manifest["public_facades"]:
        if _module_file(module_name) is None:
            raise CharacterizationError(f"public facade module is missing: {module_name}")

    return {
        "fixtures": len(manifest["fixture_sha256"]),
        "semantic_groups": len(manifest["semantic_groups"]),
        "test_nodes": node_count,
        "public_facades": len(manifest["public_facades"]),
    }


def _module_name(path: Path) -> tuple[str, bool]:
    relative = path.relative_to(SRC_ROOT).with_suffix("")
    parts = list(relative.parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _resolved_from_import(
    module_name: str,
    is_package: bool,
    node: ast.ImportFrom,
) -> list[str]:
    if node.level == 0:
        return [node.module] if node.module else []

    package_parts = module_name.split(".") if is_package else module_name.split(".")[:-1]
    parents_to_remove = node.level - 1
    if parents_to_remove > len(package_parts):
        return []
    base = package_parts[: len(package_parts) - parents_to_remove]
    if node.module:
        base.extend(node.module.split("."))
        return [".".join(base)]
    return [".".join([*base, alias.name]) for alias in node.names if alias.name != "*"]


def _classify_imports(imports: Iterable[str]) -> dict[str, list[str]]:
    internal: set[str] = set()
    standard_library: set[str] = set()
    external: set[str] = set()
    for target in imports:
        if not target:
            continue
        top_level = target.split(".", maxsplit=1)[0]
        if top_level == "ancestryllm":
            internal.add(target)
        elif top_level in sys.stdlib_module_names:
            standard_library.add(top_level)
        else:
            external.add(top_level)
    return {
        "internal": sorted(internal),
        "standard_library": sorted(standard_library),
        "external": sorted(external),
    }


def dependency_snapshot() -> dict[str, Any]:
    """Return a deterministic AST dependency graph without importing application code."""

    graph: dict[str, dict[str, list[str]]] = {}
    for path in sorted(SRC_ROOT.glob("ancestryllm/**/*.py")):
        module_name, is_package = _module_name(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        except (OSError, SyntaxError) as exc:
            raise CharacterizationError(f"cannot parse source module: {module_name}") from exc
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.extend(_resolved_from_import(module_name, is_package, node))
        graph[module_name] = _classify_imports(imports)

    try:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CharacterizationError("cannot read declared project dependencies") from exc
    declared = project.get("project", {}).get("dependencies", [])
    if not isinstance(declared, list) or any(not isinstance(item, str) for item in declared):
        raise CharacterizationError("declared dependency inventory is invalid")

    lock_path = ROOT / "uv.lock"
    snapshot: dict[str, Any] = {
        "module_graph": graph,
        "declared_runtime_dependencies": sorted(declared),
        "lockfile_sha256": _sha256(lock_path),
    }
    snapshot["digest"] = _canonical_digest(snapshot)
    return snapshot


def _source_tree_digest() -> str:
    inventory = {
        path.relative_to(ROOT).as_posix(): _sha256(path)
        for path in sorted(SRC_ROOT.glob("ancestryllm/**/*.py"))
    }
    return _canonical_digest(inventory)


def _safe_environment(temporary_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{SRC_ROOT}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC_ROOT)
    )
    environment["PYTHONHASHSEED"] = "0"
    environment["XDG_CACHE_HOME"] = str(temporary_root / "cache")
    environment["XDG_CONFIG_HOME"] = str(temporary_root / "config")
    environment["XDG_DATA_HOME"] = str(temporary_root / "data")
    return environment


def _run_semantic_groups(
    manifest: dict[str, Any],
    expected_hashes: dict[str, str],
    temporary_root: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    environment = _safe_environment(temporary_root)
    for group in manifest["semantic_groups"]:
        started = time.perf_counter()
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pytest", "-q", *group["test_nodes"]],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=False,
            check=False,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        if fixture_hashes(manifest) != expected_hashes:
            raise CharacterizationError(
                f"semantic group mutated the fixed fixture corpus: {group['id']}"
            )
        if completed.returncode != 0:
            raise CharacterizationError(
                f"semantic group failed with exit code {completed.returncode}: {group['id']}"
            )
        results.append(
            {
                "id": group["id"],
                "status": "passed",
                "test_nodes": list(group["test_nodes"]),
                "elapsed_ms": round(elapsed_ms, 3),
            }
        )
    return results


def _peak_rss_bytes(children: bool = False) -> int | None:
    if resource is None:
        return None
    target = resource.RUSAGE_CHILDREN if children else resource.RUSAGE_SELF
    value = int(resource.getrusage(target).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _measure_cli_cold_start(temporary_root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "ancestryllm", "--version"],
        cwd=ROOT,
        env=_safe_environment(temporary_root),
        capture_output=True,
        text=False,
        check=False,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    if completed.returncode != 0:
        raise CharacterizationError("CLI cold-start measurement failed")
    return {
        "elapsed_ms": round(elapsed_ms, 3),
        "peak_rss_bytes": _peak_rss_bytes(children=True),
    }


def _blocked_network(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("network access is forbidden during the offline characterization")


def _measure_offline_merge(iterations: int, temporary_root: Path) -> dict[str, Any]:
    sys.path.insert(0, str(SRC_ROOT))
    from ancestryllm.gedcom.service import GedcomService

    inputs = [
        ROOT / "tests/fixtures/gedcom_incremental/ancestry-snapshot-v1.ged",
        ROOT / "tests/fixtures/gedcom_incremental/myheritage-snapshot-v1.ged",
    ]
    output_directory = temporary_root / "merge-output"
    output_directory.mkdir(parents=True)
    service = GedcomService()
    expected_digest: str | None = None
    expected_counts: tuple[int, int] | None = None

    with (
        patch("socket.create_connection", _blocked_network),
        patch("socket.socket.connect", _blocked_network),
        patch("socket.socket.connect_ex", _blocked_network),
    ):
        service.merge(inputs, output_directory / "prewarm.ged", provider_id="none")
        started = time.perf_counter()
        for index in range(iterations):
            output = output_directory / f"result-{index}.ged"
            result = service.merge(inputs, output, provider_id="none")
            digest = _sha256(output)
            counts = (result.people_read, result.people_written)
            if expected_digest is None:
                expected_digest = digest
                expected_counts = counts
            elif digest != expected_digest or counts != expected_counts:
                raise CharacterizationError("offline merge produced nondeterministic results")
        elapsed_ms = (time.perf_counter() - started) * 1000

    return {
        "elapsed_ms": round(elapsed_ms, 3),
        "peak_rss_bytes": _peak_rss_bytes(),
        "iterations": iterations,
        "result_digest": expected_digest,
        "people_read": expected_counts[0] if expected_counts else 0,
        "people_written": expected_counts[1] if expected_counts else 0,
    }


def _worker_measurement(operation: str, iterations: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ancestryllm-characterization-worker-") as directory:
        temporary_root = Path(directory)
        if operation == "cli-cold-start":
            return _measure_cli_cold_start(temporary_root)
        if operation == "offline-gedcom-merge":
            return _measure_offline_merge(iterations, temporary_root)
    raise CharacterizationError(f"unsupported performance operation: {operation}")


def _performance_samples(
    operation: str,
    *,
    runs: int,
    iterations: int,
    temporary_root: Path,
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    environment = _safe_environment(temporary_root)
    for _ in range(runs):
        completed = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "_measure",
                operation,
                "--iterations",
                str(iterations),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise CharacterizationError(f"performance worker failed: {operation}")
        try:
            sample = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CharacterizationError(
                f"performance worker returned invalid data: {operation}"
            ) from exc
        if not isinstance(sample, dict):
            raise CharacterizationError(f"performance worker returned invalid data: {operation}")
        samples.append(sample)

    elapsed = [float(sample["elapsed_ms"]) for sample in samples]
    rss_values = [
        int(sample["peak_rss_bytes"])
        for sample in samples
        if sample.get("peak_rss_bytes") is not None
    ]
    stable_fields = {
        key: samples[0][key]
        for key in ("iterations", "result_digest", "people_read", "people_written")
        if key in samples[0]
    }
    for sample in samples[1:]:
        if any(sample.get(key) != value for key, value in stable_fields.items()):
            raise CharacterizationError(f"performance result drifted between runs: {operation}")
    return {
        "operation": operation,
        "runs": runs,
        "elapsed_ms_samples": elapsed,
        "median_elapsed_ms": round(statistics.median(elapsed), 3),
        "peak_rss_bytes_samples": rss_values,
        "median_peak_rss_bytes": int(statistics.median(rss_values)) if rss_values else None,
        **stable_fields,
    }


def performance_snapshot(manifest: dict[str, Any], temporary_root: Path) -> dict[str, Any]:
    policy = manifest["performance"]
    runs = int(policy["runs"])
    warm_iterations = int(policy["warm_iterations_per_run"])
    return {
        "cli_cold_start": _performance_samples(
            "cli-cold-start",
            runs=runs,
            iterations=1,
            temporary_root=temporary_root,
        ),
        "offline_gedcom_merge": _performance_samples(
            "offline-gedcom-merge",
            runs=runs,
            iterations=warm_iterations,
            temporary_root=temporary_root,
        ),
    }


def _git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    revision = completed.stdout.strip()
    if completed.returncode != 0 or len(revision) != 40:
        raise CharacterizationError("cannot resolve the source revision")
    return revision


def capture_report(manifest: dict[str, Any]) -> dict[str, Any]:
    inventory = verify_inventory(manifest)
    expected_hashes = fixture_hashes(manifest)
    source_tree_sha256 = _source_tree_digest()
    try:
        with tempfile.TemporaryDirectory(prefix="ancestryllm-characterization-") as directory:
            temporary_root = Path(directory)
            semantic_groups = _run_semantic_groups(manifest, expected_hashes, temporary_root)
            performance = performance_snapshot(manifest, temporary_root)
    finally:
        if fixture_hashes(manifest) != expected_hashes:
            raise CharacterizationError("characterization mutated the fixed fixture corpus")
        if _source_tree_digest() != source_tree_sha256:
            raise CharacterizationError("characterization mutated the application source tree")

    semantic_contract = [
        {
            "id": group["id"],
            "status": group["status"],
            "test_nodes": group["test_nodes"],
        }
        for group in semantic_groups
    ]
    dependencies = dependency_snapshot()
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "baseline_id": manifest["baseline_id"],
        "source_revision": _git_revision(),
        "source_tree_sha256": source_tree_sha256,
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "inventory": inventory,
        "fixture_sha256": expected_hashes,
        "fixture_digest": _canonical_digest(expected_hashes),
        "semantic_groups": semantic_groups,
        "semantic_digest": _canonical_digest(semantic_contract),
        "dependencies": dependencies,
        "performance_policy": manifest["performance"],
        "performance": performance,
    }
    return report


def _percent_change(baseline: float, candidate: float) -> float:
    if baseline == 0:
        return 0.0 if candidate == 0 else float("inf")
    return ((candidate - baseline) / baseline) * 100


def compare_reports(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Compare semantic invariants and the tracker-defined performance thresholds."""

    violations: list[dict[str, Any]] = []
    if baseline.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise CharacterizationError("unsupported baseline report schema")
    if candidate.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise CharacterizationError("unsupported candidate report schema")
    if baseline.get("baseline_id") != candidate.get("baseline_id"):
        raise CharacterizationError("reports use different characterization baselines")

    violations.extend(
        {"gate": field, "reason": "deterministic contract changed"}
        for field in ("fixture_digest", "semantic_digest")
        if baseline.get(field) != candidate.get(field)
    )

    baseline_dependencies = baseline.get("dependencies", {})
    candidate_dependencies = candidate.get("dependencies", {})
    dependency_change = {
        "baseline_digest": baseline_dependencies.get("digest"),
        "candidate_digest": candidate_dependencies.get("digest"),
        "changed": baseline_dependencies.get("digest") != candidate_dependencies.get("digest"),
    }

    policy = baseline.get("performance_policy")
    if not isinstance(policy, dict) or policy != candidate.get("performance_policy"):
        raise CharacterizationError("reports use different performance policies")

    comparisons: list[dict[str, Any]] = []
    operation_policies = {
        "cli_cold_start": {
            "minimum_ms": 0.0,
            "percent": float(policy["cold_start_regression_percent"]),
            "floor_ms": float(policy["cold_start_regression_floor_ms"]),
        },
        "offline_gedcom_merge": {
            "minimum_ms": float(policy["warm_regression_minimum_ms"]),
            "percent": float(policy["warm_regression_percent"]),
            "floor_ms": 0.0,
        },
    }
    for operation, operation_policy in operation_policies.items():
        try:
            baseline_measurement = baseline["performance"][operation]
            candidate_measurement = candidate["performance"][operation]
            baseline_elapsed = float(baseline_measurement["median_elapsed_ms"])
            candidate_elapsed = float(candidate_measurement["median_elapsed_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CharacterizationError(f"report is missing performance data: {operation}") from exc

        timing_applies = baseline_elapsed >= operation_policy["minimum_ms"]
        allowed_increase = max(
            operation_policy["floor_ms"],
            baseline_elapsed * operation_policy["percent"] / 100,
        )
        timing_limit = baseline_elapsed + allowed_increase
        timing_passed = not timing_applies or candidate_elapsed <= timing_limit
        timing_comparison = {
            "gate": f"{operation}.median_elapsed_ms",
            "baseline": baseline_elapsed,
            "candidate": candidate_elapsed,
            "percent_change": round(_percent_change(baseline_elapsed, candidate_elapsed), 3),
            "limit": round(timing_limit, 3),
            "applies": timing_applies,
            "passed": timing_passed,
        }
        comparisons.append(timing_comparison)
        if not timing_passed:
            violations.append(
                {
                    "gate": timing_comparison["gate"],
                    "reason": "performance regression exceeded threshold",
                }
            )

        baseline_rss = baseline_measurement.get("median_peak_rss_bytes")
        candidate_rss = candidate_measurement.get("median_peak_rss_bytes")
        rss_percent = float(policy["peak_rss_regression_percent"])
        rss_passed = (baseline_rss is None and candidate_rss is None) or (
            baseline_rss is not None
            and candidate_rss is not None
            and float(candidate_rss) <= float(baseline_rss) * (1 + rss_percent / 100)
        )
        rss_comparison = {
            "gate": f"{operation}.median_peak_rss_bytes",
            "baseline": baseline_rss,
            "candidate": candidate_rss,
            "percent_change": (
                round(_percent_change(float(baseline_rss), float(candidate_rss)), 3)
                if baseline_rss is not None and candidate_rss is not None
                else None
            ),
            "limit": (
                int(float(baseline_rss) * (1 + rss_percent / 100))
                if baseline_rss is not None
                else None
            ),
            "applies": baseline_rss is not None,
            "passed": rss_passed,
        }
        comparisons.append(rss_comparison)
        if not rss_passed:
            violations.append(
                {
                    "gate": rss_comparison["gate"],
                    "reason": "peak RSS regression exceeded threshold or measurement is missing",
                }
            )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "baseline_id": baseline["baseline_id"],
        "passed": not violations,
        "semantic": {
            "fixture_digest_unchanged": baseline.get("fixture_digest")
            == candidate.get("fixture_digest"),
            "semantic_digest_unchanged": baseline.get("semantic_digest")
            == candidate.get("semantic_digest"),
        },
        "dependency_change": dependency_change,
        "performance": comparisons,
        "violations": violations,
    }


def _external_output_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return path
    raise argparse.ArgumentTypeError(
        "characterization reports must be written outside the repository"
    )


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify", help="verify the committed inventory without executing tests")

    capture = subparsers.add_parser("capture", help="capture a complete characterization report")
    capture.add_argument("--output", required=True, type=_external_output_path)

    compare = subparsers.add_parser("compare", help="compare two characterization reports")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--output", required=True, type=_external_output_path)

    measure = subparsers.add_parser("_measure", help=argparse.SUPPRESS)
    measure.add_argument(
        "operation",
        choices=("cli-cold-start", "offline-gedcom-merge"),
    )
    measure.add_argument("--iterations", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "_measure":
            result = _worker_measurement(args.operation, args.iterations)
            print(json.dumps(result, sort_keys=True))
            return 0

        manifest = load_manifest()
        if args.command == "verify":
            counts = verify_inventory(manifest)
            print(
                "core-contracts characterization inventory verified "
                f"({counts['fixtures']} fixtures, {counts['test_nodes']} test nodes)"
            )
            return 0
        if args.command == "capture":
            _write_json(args.output, capture_report(manifest))
            print(f"core-contracts characterization captured: {args.output}")
            return 0
        if args.command == "compare":
            comparison = compare_reports(
                _load_json(args.baseline),
                _load_json(args.candidate),
            )
            _write_json(args.output, comparison)
            if comparison["passed"]:
                print(f"core-contracts characterization comparison passed: {args.output}")
                return 0
            print(f"core-contracts characterization comparison failed: {args.output}")
            return 1
    except CharacterizationError as exc:
        print(f"characterization failed: {exc}", file=sys.stderr)
        return 2
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
