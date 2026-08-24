"""Verify the Semgrep runner authenticates, hashes, and invokes the pinned release."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "run_pinned_semgrep.py"


class _Download:
    def __init__(self, content: bytes, url: str) -> None:
        self._content = content
        self._url = url

    def __enter__(self) -> _Download:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, _limit: int) -> bytes:
        return self._content


class _YamlError(Exception):
    """Stand-in for PyYAML's parser error in the dependency-light test profile."""


def _json_safe_load(payload: bytes | str) -> object:
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _YamlError from error


def _json_safe_dump(document: object, *, sort_keys: bool = False) -> str:
    return json.dumps(document, sort_keys=sort_keys)


def _rule_payload(*rules: dict[str, object], missed: int | None = None) -> bytes:
    document: dict[str, Any] = {"rules": list(rules)}
    if missed is not None:
        document["missed"] = missed
    return json.dumps(document, separators=(",", ":")).encode()


def _load_runner() -> ModuleType:
    assert _SCRIPT.is_file(), "the pinned Semgrep runner must be checked in"
    spec = importlib.util.spec_from_file_location("run_pinned_semgrep", _SCRIPT)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    yaml_stub = ModuleType("yaml")
    yaml_stub.YAMLError = _YamlError
    yaml_stub.safe_load = _json_safe_load
    yaml_stub.safe_dump = _json_safe_dump
    spec.loader.exec_module(runner)
    runner._yaml_module = lambda: yaml_stub
    return runner


def _semantic_rule_sha256(payload: bytes) -> str:
    document = json.loads(payload)
    assert isinstance(document, dict)
    rules = document.get("rules")
    assert isinstance(rules, list)
    normalized = dict(document)
    normalized.pop("missed", None)
    normalized["rules"] = sorted(rules, key=lambda rule: rule["id"])
    canonical = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


@pytest.mark.parametrize(
    ("response_content", "response_url", "expected_error"),
    (
        (b"changed content here", "https://semgrep.dev/c/p/test", "content hash differs"),
        (_rule_payload({"id": "pinned"}), "https://semgrep.dev/c/p/redirected", "redirected"),
    ),
)
def test_rejects_changed_or_redirected_registry_content_before_writing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response_content: bytes,
    response_url: str,
    expected_error: str,
) -> None:
    runner = _load_runner()
    expected = _rule_payload({"id": "pinned"})
    bundle = runner.RuleBundle(
        name="test",
        url="https://semgrep.dev/c/p/test",
        semantic_sha256=_semantic_rule_sha256(expected),
        revisions=(
            runner.RuleRevision(
                sha256=hashlib.sha256(expected).hexdigest(),
                size=len(expected),
            ),
        ),
    )
    monkeypatch.setattr(
        runner.urllib.request,
        "urlopen",
        lambda request, timeout: _Download(response_content, response_url),
    )
    destination = tmp_path / "test.yml"

    with pytest.raises(RuntimeError, match=expected_error):
        runner.download_rule_bundle(bundle, destination)

    assert not destination.exists()


def test_rejects_changed_registry_size_before_writing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner()
    payload = _rule_payload({"id": "pinned"})
    bundle = runner.RuleBundle(
        name="test",
        url="https://semgrep.dev/c/p/test",
        semantic_sha256=_semantic_rule_sha256(payload),
        revisions=(
            runner.RuleRevision(
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload) + 1,
            ),
        ),
    )
    monkeypatch.setattr(
        runner.urllib.request,
        "urlopen",
        lambda request, timeout: _Download(payload, request.full_url),
    )
    destination = tmp_path / "test.yml"

    with pytest.raises(RuntimeError, match="size differs"):
        runner.download_rule_bundle(bundle, destination)

    assert not destination.exists()


@pytest.mark.parametrize("revision_index", (0, 1))
def test_accepts_each_reviewed_exact_bundle_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision_index: int,
) -> None:
    runner = _load_runner()
    payloads = (
        _rule_payload({"id": "zed"}, {"id": "alpha"}),
        _rule_payload({"id": "alpha"}, {"id": "zed"}, missed=923),
    )
    bundle = runner.RuleBundle(
        name="test",
        url="https://semgrep.dev/c/p/test",
        semantic_sha256=_semantic_rule_sha256(payloads[0]),
        revisions=tuple(
            runner.RuleRevision(
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload),
            )
            for payload in payloads
        ),
    )
    monkeypatch.setattr(
        runner.urllib.request,
        "urlopen",
        lambda request, timeout: _Download(payloads[revision_index], request.full_url),
    )
    destination = tmp_path / "test.yml"

    runner.download_rule_bundle(bundle, destination)

    assert destination.read_bytes() == payloads[revision_index]


def test_rejects_raw_pinned_bundle_with_changed_semantic_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner()
    reviewed = _rule_payload({"id": "pinned", "message": "reviewed"})
    changed = _rule_payload({"id": "pinned", "message": "changed"})
    bundle = runner.RuleBundle(
        name="test",
        url="https://semgrep.dev/c/p/test",
        semantic_sha256=_semantic_rule_sha256(reviewed),
        revisions=(
            runner.RuleRevision(
                sha256=hashlib.sha256(changed).hexdigest(),
                size=len(changed),
            ),
        ),
    )
    calls = 0

    def urlopen(request: object, timeout: int) -> _Download:
        nonlocal calls
        calls += 1
        return _Download(changed, "https://semgrep.dev/c/p/test")

    monkeypatch.setattr(runner.urllib.request, "urlopen", urlopen)
    destination = tmp_path / "test.yml"

    with pytest.raises(RuntimeError, match="semantic content differs"):
        runner.download_rule_bundle(bundle, destination)

    assert calls == 3
    assert not destination.exists()


def test_retries_transient_unreviewed_registry_content_before_accepting_reviewed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner()
    reviewed = _rule_payload({"id": "pinned"})
    responses = iter((b"transient edge response", reviewed))
    bundle = runner.RuleBundle(
        name="test",
        url="https://semgrep.dev/c/p/test",
        semantic_sha256=_semantic_rule_sha256(reviewed),
        revisions=(
            runner.RuleRevision(
                sha256=hashlib.sha256(reviewed).hexdigest(),
                size=len(reviewed),
            ),
        ),
    )
    calls = 0

    def urlopen(request: object, timeout: int) -> _Download:
        nonlocal calls
        calls += 1
        assert timeout == 60
        return _Download(next(responses), "https://semgrep.dev/c/p/test")

    monkeypatch.setattr(runner.urllib.request, "urlopen", urlopen)
    destination = tmp_path / "test.yml"

    runner.download_rule_bundle(bundle, destination)

    assert calls == 2
    assert destination.read_bytes() == reviewed


def test_reports_observed_content_after_bounded_registry_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner()
    reviewed = _rule_payload({"id": "pinned"})
    unreviewed = b"persistently changed content"
    bundle = runner.RuleBundle(
        name="test",
        url="https://semgrep.dev/c/p/test",
        semantic_sha256=_semantic_rule_sha256(reviewed),
        revisions=(
            runner.RuleRevision(
                sha256=hashlib.sha256(reviewed).hexdigest(),
                size=len(reviewed),
            ),
        ),
    )
    calls = 0

    def urlopen(request: object, timeout: int) -> _Download:
        nonlocal calls
        calls += 1
        return _Download(unreviewed, "https://semgrep.dev/c/p/test")

    monkeypatch.setattr(runner.urllib.request, "urlopen", urlopen)
    destination = tmp_path / "test.yml"
    digest = hashlib.sha256(unreviewed).hexdigest()

    with pytest.raises(
        RuntimeError,
        match=rf"after 3 attempts; observed sha256={digest}, size={len(unreviewed)}",
    ):
        runner.download_rule_bundle(bundle, destination)

    assert calls == 3
    assert not destination.exists()


def test_resolves_semgrep_beside_the_script_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner()
    interpreter = tmp_path / "python"
    semgrep = tmp_path / "semgrep"
    semgrep.touch()
    monkeypatch.setattr(runner.sys, "executable", str(interpreter))

    assert runner._semgrep_executable() == semgrep


def test_configures_reviewed_python_registry_bundle() -> None:
    runner = _load_runner()

    bundles = {bundle.name: bundle for bundle in runner.RULE_BUNDLES}
    python = bundles["python"]

    assert python.url == "https://semgrep.dev/c/p/python"
    assert (
        python.semantic_sha256 == "80eeb6e5e772926c4fb1e0c6dd49def6d197305127e002a6bdb24b35bf3e6b80"
    )
    assert (
        runner.RuleRevision(
            sha256="c030c27616041435f0c1f7bfc6d18b9241401c3c1936ad7380229b692c8473d6",
            size=487_962,
        )
        in python.revisions
    )
    assert (
        runner.RuleRevision(
            sha256="449934d9be1f6f2ea91ff5175a8629cc93c38f8f52ca3da44e43089bcca44260",
            size=487_962,
        )
        in python.revisions
    )
    assert (
        runner.RuleRevision(
            sha256="0b7d2717d79da2ce99bffa329d954833e2ecc034b7ff86f0932a38ce416b5946",
            size=487_962,
        )
        in python.revisions
    )
    assert (
        runner.RuleRevision(
            sha256="e9f042a0c53c33e67ad43288b548a52a20b23dc3f0feea7bfffaba17cc63949a",
            size=487_962,
        )
        in python.revisions
    )
    assert (
        runner.RuleRevision(
            sha256="2fd889488c318acc98cdf84fc3736ed5b9b20c68ca5ea619af35cac716f961e7",
            size=487_962,
        )
        in python.revisions
    )
    assert (
        runner.RuleRevision(
            sha256="5bf4a3a5080baa129b0080d2deb1edbe140464a48bced044901c69fdf581d71d",
            size=487_962,
        )
        in python.revisions
    )
    assert (
        runner.RuleRevision(
            sha256="babf8c5994a3074d041077174ae3f5d14a88e807084ece1a53a29c7cbbdf5851",
            size=487_962,
        )
        in python.revisions
    )
    assert (
        runner.RuleRevision(
            sha256="18d4f94e9cbd3944c752dcad2d9b643b562d37abe12705c61176fd9a7cc1f0c1",
            size=487_962,
        )
        in python.revisions
    )
    assert (
        runner.RuleRevision(
            sha256="a8a155d58dc346b4358e9f9e347d93c0869a542304a216b9f63701ea1d737b12",
            size=487_962,
        )
        in python.revisions
    )
    assert (
        runner.RuleRevision(
            sha256="c8a84a1877ad8c93e189377b80c798ed27aec80ee85a977409af05a526c54b4b",
            size=487_962,
        )
        in python.revisions
    )
    assert (
        runner.RuleRevision(
            sha256="7476830701b31be1c4a76432e23950b86ec76635888103877ca24708e7c01c76",
            size=487_962,
        )
        in python.revisions
    )


def test_configures_reviewed_trailofbits_registry_bundle() -> None:
    runner = _load_runner()

    bundles = {bundle.name: bundle for bundle in runner.RULE_BUNDLES}
    trailofbits = bundles["trailofbits"]

    assert trailofbits.url == "https://semgrep.dev/c/p/trailofbits"
    assert (
        trailofbits.semantic_sha256
        == "8b1a7d47796f1d002fa6ca1a960efefaf775b2d0cd864a413aa23a5210ed59ea"
    )
    assert trailofbits.revisions == (
        runner.RuleRevision(
            sha256="d734464c3a746401a5293d44bec10a2be2bf718814b52ef3bd0f284f52863343",
            size=185_537,
        ),
    )
    assert len(trailofbits.include_rule_ids) == 10
    assert all(
        rule_id.startswith("trailofbits.generic.") for rule_id in trailofbits.include_rule_ids
    )


def test_configures_commit_pinned_reviewed_archives() -> None:
    runner = _load_runner()

    archives = {archive.name: archive for archive in runner.RULE_ARCHIVES}

    assert set(archives) == {"apiiro-prevent", "elttam"}
    assert len(archives["apiiro-prevent"].members) == 12
    assert archives["apiiro-prevent"].url.endswith("a21246b666f34db899f0e33add7237ed70fab790")
    assert archives["elttam"].members == (
        "rules/yaml/github-actions/security/save-state.yaml",
        "rules/yaml/github-actions/security/set-output.yaml",
    )


def test_selects_only_reviewed_registry_rules_and_rejects_missing_ids() -> None:
    runner = _load_runner()
    payload = _rule_payload({"id": "selected"}, {"id": "irrelevant"})
    bundle = runner.RuleBundle(
        name="test",
        url="https://semgrep.dev/c/p/test",
        semantic_sha256=_semantic_rule_sha256(payload),
        revisions=(),
        include_rule_ids=("selected",),
    )

    assert [rule["id"] for rule in runner._selected_bundle_rules(bundle, payload)] == ["selected"]

    missing = runner.RuleBundle(
        name="test",
        url=bundle.url,
        semantic_sha256=bundle.semantic_sha256,
        revisions=(),
        include_rule_ids=("not-present",),
    )
    with pytest.raises(RuntimeError, match="missing reviewed rules: not-present"):
        runner._selected_bundle_rules(missing, payload)


def test_reads_only_reviewed_archive_members_and_checks_semantic_digest() -> None:
    runner = _load_runner()
    reviewed = _rule_payload({"id": "reviewed", "message": "useful"})
    ignored = _rule_payload({"id": "ignored"})
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in (("root/reviewed.yml", reviewed), ("root/ignored.yml", ignored)):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    semantic = _semantic_rule_sha256(reviewed)
    archive = runner.RuleArchive(
        name="test",
        url="https://codeload.github.com/example/rules/tar.gz/commit",
        semantic_sha256=semantic,
        revision=runner.RuleRevision(
            sha256=hashlib.sha256(buffer.getvalue()).hexdigest(), size=len(buffer.getvalue())
        ),
        members=("reviewed.yml",),
    )

    assert runner._archive_rules(archive, buffer.getvalue()) == [
        {"id": "reviewed", "message": "useful"}
    ]


def test_retries_archive_failures_before_accepting_reviewed_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    reviewed = b"reviewed archive bytes"
    responses = iter((TimeoutError("temporary timeout"), b"unreviewed", reviewed))
    archive = runner.RuleArchive(
        name="test",
        url="https://codeload.github.com/example/rules/tar.gz/commit",
        semantic_sha256="unused",
        revision=runner.RuleRevision(
            sha256=hashlib.sha256(reviewed).hexdigest(), size=len(reviewed)
        ),
        members=(),
    )
    calls = 0

    def urlopen(request: object, timeout: int) -> _Download:
        nonlocal calls
        calls += 1
        assert timeout == 60
        response = next(responses)
        if isinstance(response, BaseException):
            raise response
        return _Download(response, archive.url)

    monkeypatch.setattr(runner.urllib.request, "urlopen", urlopen)

    assert runner._download_rule_archive(archive) == reviewed
    assert calls == 3


def test_reports_observed_content_after_bounded_archive_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    reviewed = b"reviewed archive bytes"
    unreviewed = b"persistently changed archive"
    archive = runner.RuleArchive(
        name="test",
        url="https://codeload.github.com/example/rules/tar.gz/commit",
        semantic_sha256="unused",
        revision=runner.RuleRevision(
            sha256=hashlib.sha256(reviewed).hexdigest(), size=len(reviewed)
        ),
        members=(),
    )
    calls = 0

    def urlopen(request: object, timeout: int) -> _Download:
        nonlocal calls
        calls += 1
        return _Download(unreviewed, archive.url)

    monkeypatch.setattr(runner.urllib.request, "urlopen", urlopen)
    digest = hashlib.sha256(unreviewed).hexdigest()

    with pytest.raises(
        RuntimeError,
        match=rf"after 3 attempts; observed sha256={digest}, size={len(unreviewed)}",
    ):
        runner._download_rule_archive(archive)

    assert calls == 3


def test_deduplicates_identical_ids_and_matching_logic_but_rejects_collisions() -> None:
    runner = _load_runner()
    first = {"id": "first", "message": "one", "severity": "WARNING", "pattern": "danger()"}
    same_id = dict(first)
    same_logic = {"id": "second", "message": "two", "severity": "ERROR", "pattern": "danger()"}

    rules, duplicates = runner._deduplicate_rules(((first, same_id, same_logic),))

    assert rules == [first]
    assert duplicates == 2
    with pytest.raises(ValueError, match="id collision has different content: first"):
        runner._deduplicate_rules(((first, {**first, "message": "changed"}),))
    with pytest.raises(ValueError, match="id collision has different content: second"):
        runner._deduplicate_rules(((first, same_logic, {**same_logic, "message": "changed"}),))


@pytest.mark.parametrize("invalid_rule_id", (None, 1, "", "   "))
def test_deduplication_rejects_invalid_rule_ids(invalid_rule_id: object) -> None:
    runner = _load_runner()

    with pytest.raises(ValueError, match="rule id must be a non-empty string"):
        runner._deduplicate_rules((({"id": invalid_rule_id, "pattern": "danger()"},),))


def test_runs_locked_semgrep_with_verified_temporary_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    payloads = {
        "https://semgrep.dev/c/p/python": _rule_payload({"id": "python", "pattern": "python()"}),
        "https://semgrep.dev/c/p/secrets": _rule_payload({"id": "secrets", "pattern": "secret()"}),
    }
    bundles = tuple(
        runner.RuleBundle(
            name=name,
            url=url,
            semantic_sha256=_semantic_rule_sha256(payloads[url]),
            revisions=(
                runner.RuleRevision(
                    sha256=hashlib.sha256(payloads[url]).hexdigest(),
                    size=len(payloads[url]),
                ),
            ),
        )
        for name, url in (
            ("python", "https://semgrep.dev/c/p/python"),
            ("secrets", "https://semgrep.dev/c/p/secrets"),
        )
    )
    monkeypatch.setattr(runner, "RULE_BUNDLES", bundles)
    monkeypatch.setattr(runner, "RULE_ARCHIVES", ())
    monkeypatch.setattr(
        runner.urllib.request,
        "urlopen",
        lambda request, timeout: _Download(payloads[request.full_url], request.full_url),
    )
    monkeypatch.setattr(runner, "_semgrep_executable", lambda: Path("/locked/semgrep"))
    calls: list[list[str]] = []
    config_paths: list[Path] = []
    runtime_paths: list[Path] = []

    def run(command: list[str], *, check: bool, env: dict[str, str]) -> SimpleNamespace:
        assert check is False
        runtime_paths.extend((Path(env["SEMGREP_LOG_FILE"]), Path(env["SEMGREP_SETTINGS_FILE"])))
        config_index = command.index("--config") + 1
        assert all(path.parent == Path(command[config_index]).parent for path in runtime_paths)
        for path in runtime_paths:
            path.touch()
        calls.append(command)
        config_paths.append(Path(command[config_index]))
        document = json.loads(config_paths[0].read_text(encoding="utf-8"))
        assert [rule["id"] for rule in document["rules"]] == ["python", "secrets"]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", run)

    assert runner.main(["src"]) == 0
    assert calls == [
        [
            "/locked/semgrep",
            "scan",
            "--error",
            "--metrics=off",
            "--disable-version-check",
            "--config",
            str(config_paths[0]),
            "src",
        ]
    ]
    assert all(not path.exists() for path in (*config_paths, *runtime_paths))
