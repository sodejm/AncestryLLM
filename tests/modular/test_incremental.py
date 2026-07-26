from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from ancestryllm.core.errors import ProviderError
from ancestryllm.core.ingress import (
    FileIngressPolicy,
    FileKind,
    FileSnapshot,
    TextLine,
)
from ancestryllm.gedcom import engine, incremental
from ancestryllm.gedcom.sync import run_sync

FIXTURES = Path(__file__).parents[1] / "fixtures" / "gedcom_incremental"


def test_incremental_argument_errors_use_stable_typed_contract() -> None:
    parser = incremental.PlainEnglishArgumentParser(prog="ancestry gedcom update")
    with pytest.raises(incremental.SyncError) as raised:
        parser.error("missing --master")
    assert raised.value.code == "SYNC_CONFIGURATION"
    assert raised.value.exit_code == 2


def test_incremental_normalization_boundary_returns_strings() -> None:
    assert incremental._normal_value("DATE", "July 15, 1850", engine) == "15 JUL 1850"
    assert incremental._normal_value("CTRY", "USA", engine) == "united states"


def _snapshot(name: str, vendor: str, version: int) -> str:
    return f"{name}:{vendor}={FIXTURES / f'{vendor}-snapshot-v{version}.ged'}"


def _initialize_release(releases: Path) -> Path:
    assert (
        run_sync(
            [
                "update",
                "--master",
                str(FIXTURES / "baseline-master.ged"),
                "--initialize-manifest",
                "--snapshot",
                _snapshot("ancestry-main", "ancestry", 1),
                "--snapshot",
                _snapshot("myheritage-main", "myheritage", 1),
                "--exported-at",
                "ancestry-main=2025-01-15",
                "--exported-at",
                "myheritage-main=2025-02-03",
                "--release-root",
                str(releases),
                "--no-quality-report",
            ]
        )
        == 0
    )
    return next(releases.glob("g0001-*"))


def _update_args(releases: Path, bundle: Path, *snapshots: str) -> list[str]:
    return [
        "update",
        "--master",
        str(bundle / "master.ged"),
        "--manifest",
        str(bundle / "manifest.json"),
        "--release-root",
        str(releases),
        "--no-quality-report",
        *(item for snapshot in snapshots for item in ("--snapshot", snapshot)),
    ]


def _write_ambiguous_person(path: Path, *, surname: str, birth: str) -> None:
    path.write_text(
        "0 HEAD\n"
        "1 GEDC\n"
        "2 VERS 5.5.5\n"
        "1 CHAR UTF-8\n"
        "0 @I1@ INDI\n"
        f"1 NAME John /{surname}/\n"
        "1 BIRT\n"
        f"2 DATE {birth}\n"
        "2 PLAC Boston, Massachusetts, USA\n"
        "0 TRLR\n",
        encoding="utf-8",
    )


class _ReplaceAfterHashPolicy(FileIngressPolicy):
    def __init__(self, target: Path, replacement: Path) -> None:
        super().__init__()
        self.target = target.absolute()
        self.replacement = replacement
        self.replaced = False

    def sha256(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        expected: FileSnapshot | None = None,
    ) -> tuple[str, FileSnapshot]:
        result = super().sha256(path, kind, expected=expected)
        if Path(path).absolute() == self.target and not self.replaced:
            os.replace(self.replacement, self.target)
            self.replaced = True
        return result


class _ReplaceAfterJsonPolicy(FileIngressPolicy):
    def __init__(self, target: Path, replacement: Path) -> None:
        super().__init__()
        self.target = target.absolute()
        self.replacement = replacement
        self.replaced = False

    def read_json(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        require_object: bool = False,
        expected: FileSnapshot | None = None,
    ) -> object:
        value = super().read_json(
            path,
            kind,
            require_object=require_object,
            expected=expected,
        )
        if Path(path).absolute() == self.target and not self.replaced:
            os.replace(self.replacement, self.target)
            self.replaced = True
        return value


class _ReplaceAfterTextReadPolicy(FileIngressPolicy):
    def __init__(self, target: Path, replacement: Path) -> None:
        super().__init__()
        self.target = target.absolute()
        self.replacement = replacement
        self.replaced = False

    def iter_text_line_items(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        count_lines_as_records: bool = True,
        expected: FileSnapshot | None = None,
    ) -> Iterator[TextLine]:
        yield from super().iter_text_line_items(
            path,
            kind,
            count_lines_as_records=count_lines_as_records,
            expected=expected,
        )
        if Path(path).absolute() == self.target and not self.replaced:
            os.replace(self.replacement, self.target)
            self.replaced = True


def _replace_copy(path: Path, old: bytes, new: bytes) -> Path:
    replacement = path.with_name(path.name + ".replacement")
    payload = path.read_bytes()
    assert len(old) == len(new)
    assert old in payload
    replacement.write_bytes(payload.replace(old, new, 1))
    assert replacement.stat().st_size == path.stat().st_size
    return replacement


def test_update_rejects_snapshot_replaced_after_verified_hash(tmp_path: Path) -> None:
    master = tmp_path / "master.ged"
    master.write_bytes((FIXTURES / "baseline-master.ged").read_bytes())
    snapshot = tmp_path / "snapshot.ged"
    snapshot.write_bytes((FIXTURES / "ancestry-snapshot-v1.ged").read_bytes())
    replacement = _replace_copy(snapshot, b"Mira", b"Zira")
    releases = tmp_path / "releases"
    policy = _ReplaceAfterHashPolicy(snapshot, replacement)

    status = run_sync(
        [
            "update",
            "--master",
            str(master),
            "--initialize-manifest",
            "--snapshot",
            f"fictional:other={snapshot}",
            "--release-root",
            str(releases),
            "--no-quality-report",
        ],
        policy,
    )

    assert status == 2
    assert not list(releases.glob("g*-*"))
    assert not list(releases.glob(".gedcom-sync-*"))


def test_update_rejects_master_replaced_after_verified_hash(tmp_path: Path) -> None:
    master = tmp_path / "master.ged"
    master.write_bytes((FIXTURES / "baseline-master.ged").read_bytes())
    replacement = _replace_copy(master, b"Mira", b"Zira")
    releases = tmp_path / "releases"
    policy = _ReplaceAfterHashPolicy(master, replacement)

    status = run_sync(
        [
            "update",
            "--master",
            str(master),
            "--initialize-manifest",
            "--snapshot",
            _snapshot("ancestry-main", "ancestry", 1),
            "--release-root",
            str(releases),
            "--no-quality-report",
        ],
        policy,
    )

    assert status == 2
    assert not list(releases.glob("g*-*"))


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_sync_rejects_manifest_replaced_after_parse(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    manifest = first / "manifest.json"
    replacement = _replace_copy(manifest, b'"generation": 1', b'"generation": 9')
    policy = _ReplaceAfterJsonPolicy(manifest, replacement)
    if operation == "update":
        arguments = _update_args(
            releases,
            first,
            _snapshot("ancestry-main", "ancestry", 2),
        )
    else:
        edited = tmp_path / "edited.ged"
        edited.write_bytes((first / "master.ged").read_bytes())
        arguments = [
            "rebase",
            "--master",
            str(edited),
            "--manifest",
            str(manifest),
            "--release-root",
            str(releases),
            "--reason",
            "Fictional manifest race regression",
        ]

    assert run_sync(arguments, policy) == 2
    assert len(list(releases.glob("g*-*"))) == 1


def test_rebase_rejects_master_replaced_after_parse_before_copy(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    edited = tmp_path / "edited.ged"
    edited.write_bytes((first / "master.ged").read_bytes())
    replacement = _replace_copy(edited, b"Mira", b"Zira")
    policy = _ReplaceAfterTextReadPolicy(edited, replacement)

    status = run_sync(
        [
            "rebase",
            "--master",
            str(edited),
            "--manifest",
            str(first / "manifest.json"),
            "--release-root",
            str(releases),
            "--reason",
            "Fictional master race regression",
        ],
        policy,
    )

    assert status == 2
    assert len(list(releases.glob("g*-*"))) == 1


def test_incremental_initialization_is_offline_and_publishes_atomic_bundle(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    with (
        patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "must-not-be-read", "GEMINI_API_KEY": "must-not-be-read"},
        ),
        patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network forbidden"),
        ),
    ):
        status = run_sync(
            [
                "update",
                "--master",
                str(FIXTURES / "baseline-master.ged"),
                "--initialize-manifest",
                "--snapshot",
                f"ancestry-main:ancestry={FIXTURES / 'ancestry-snapshot-v1.ged'}",
                "--exported-at",
                "ancestry-main=2025-01-15",
                "--release-root",
                str(releases),
                "--no-quality-report",
            ]
        )
    assert status == 0
    bundles = list(releases.glob("g0001-*"))
    assert len(bundles) == 1
    assert {path.name for path in bundles[0].iterdir()} == {
        "master.ged",
        "manifest.json",
        "update.md",
        "quality.md",
        "rollback.json",
    }
    manifest = json.loads((bundles[0] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation"] == 1
    assert "LLM provider: `none` (offline deterministic)" in (bundles[0] / "update.md").read_text(
        encoding="utf-8"
    )


def test_incremental_update_uses_the_injected_modular_resolver(tmp_path: Path) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    releases = tmp_path / "releases"
    _write_ambiguous_person(master, surname="Smith", birth="1850")
    _write_ambiguous_person(snapshot, surname="Smyth", birth="1851")
    selections: list[tuple[str, str, str | None]] = []
    pairs: list[tuple[str, str]] = []

    def resolver_factory(provider: str, model: str, consent: str | None):
        selections.append((provider, model, consent))

        def resolve(left, right):
            pairs.append((left.pointer, right.pointer))
            return {
                "is_duplicate": True,
                "confidence": 0.95,
                "reasoning": "Fictional records agree.",
                "preferred_values": {},
                "_provider": provider,
                "_model": model,
            }

        return resolve

    assert (
        run_sync(
            [
                "update",
                "--master",
                str(master),
                "--initialize-manifest",
                "--snapshot",
                f"fictional-main:other={snapshot}",
                "--release-root",
                str(releases),
                "--no-quality-report",
                "--provider",
                "ollama",
                "--model",
                "fixture-model",
                "--consent",
                "fixture-consent",
            ],
            resolver_factory=resolver_factory,
        )
        == 0
    )

    bundle = next(releases.glob("g0001-*"))
    assert selections == [("ollama", "fixture-model", "fixture-consent")]
    assert len(pairs) == 1
    assert (bundle / "master.ged").read_text(encoding="utf-8").count(" INDI") == 1
    report = (bundle / "update.md").read_text(encoding="utf-8")
    assert "LLM provider: `ollama` (explicit opt-in)" in report
    assert "LLM ollama/fixture-model" in report


def test_incremental_provider_failure_propagates_without_publishing(tmp_path: Path) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    releases = tmp_path / "releases"
    _write_ambiguous_person(master, surname="Smith", birth="1850")
    _write_ambiguous_person(snapshot, surname="Smyth", birth="1851")

    def resolver_factory(_provider: str, _model: str, _consent: str | None):
        def fail(_left, _right):
            raise ProviderError(
                "PROVIDER_TIMEOUT",
                "The ollama request timed out before output began.",
                details={"error_type": "TimeoutError"},
            )

        return fail

    with pytest.raises(ProviderError) as raised:
        run_sync(
            [
                "update",
                "--master",
                str(master),
                "--initialize-manifest",
                "--snapshot",
                f"fictional-main:other={snapshot}",
                "--release-root",
                str(releases),
                "--no-quality-report",
                "--provider",
                "ollama",
                "--model",
                "fixture-model",
            ],
            resolver_factory=resolver_factory,
        )

    assert raised.value.code == "PROVIDER_TIMEOUT"
    assert not list(releases.glob("g*-*"))


def test_incremental_rejects_the_retired_direct_backend_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert (
        run_sync(
            [
                "update",
                "--master",
                str(FIXTURES / "baseline-master.ged"),
                "--initialize-manifest",
                "--snapshot",
                _snapshot("ancestry-main", "ancestry", 1),
                "--release-root",
                str(tmp_path / "releases"),
                "--no-quality-report",
                "--ai-backend",
                "ollama",
            ]
        )
        == 2
    )


def test_incremental_active_snapshot_is_idempotent(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    first_args = [
        "update",
        "--master",
        str(FIXTURES / "baseline-master.ged"),
        "--initialize-manifest",
        "--snapshot",
        f"ancestry-main:ancestry={FIXTURES / 'ancestry-snapshot-v1.ged'}",
        "--release-root",
        str(releases),
        "--no-quality-report",
    ]
    assert run_sync(first_args) == 0
    bundle = next(releases.glob("g0001-*"))
    assert (
        run_sync(
            [
                "update",
                "--master",
                str(bundle / "master.ged"),
                "--manifest",
                str(bundle / "manifest.json"),
                "--snapshot",
                f"ancestry-main:ancestry={FIXTURES / 'ancestry-snapshot-v1.ged'}",
                "--release-root",
                str(releases),
                "--no-quality-report",
            ]
        )
        == 0
    )
    assert len(list(releases.glob("g*-*"))) == 1


def test_incremental_replaces_changed_snapshots_and_preserves_snapshot_history(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)

    assert (
        run_sync(
            _update_args(
                releases,
                first,
                _snapshot("ancestry-main", "ancestry", 2),
                _snapshot("myheritage-main", "myheritage", 2),
            )
        )
        == 0
    )

    second = next(releases.glob("g0002-*"))
    manifest = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation"] == 2
    assert len(manifest["snapshots"]) == 4
    assert manifest["active_snapshots"]["ancestry-main"] in manifest["snapshots"]
    assert manifest["active_snapshots"]["myheritage-main"] in manifest["snapshots"]
    assert manifest["snapshots"][manifest["active_snapshots"]["ancestry-main"]]["path"].endswith(
        "ancestry-snapshot-v2.ged"
    )

    master = (second / "master.ged").read_text(encoding="utf-8")
    assert "0 @I100@ INDI" in master
    assert "Ilyan /Shore/" in master
    assert "@AX-15@" not in master
    assert "School librarian" not in master
    assert "Cedar Bay, Vermont, United States" in master


def test_rebase_requires_confirmation_then_tombstones_manual_deletions(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    edited_master = tmp_path / "fictional-manual-deletion.ged"
    edited_master.write_text(
        (first / "master.ged").read_text(encoding="utf-8").replace("1 OCCU Cartographer\n", ""),
        encoding="utf-8",
    )
    rebase_args = [
        "rebase",
        "--master",
        str(edited_master),
        "--manifest",
        str(first / "manifest.json"),
        "--release-root",
        str(releases),
        "--reason",
        "Fictional manual correction for regression coverage",
    ]

    def forbidden_resolver_factory(_provider: str, _model: str, _consent: str | None) -> None:
        raise AssertionError("rebase must never construct a provider resolver")

    assert run_sync(rebase_args, resolver_factory=forbidden_resolver_factory) == 6
    assert len(list(releases.glob("g*-*"))) == 1

    assert (
        run_sync(
            [*rebase_args, "--accept-manual-deletions"],
            resolver_factory=forbidden_resolver_factory,
        )
        == 0
    )
    rebased = next(releases.glob("g0002-*"))
    rebase_manifest = json.loads((rebased / "manifest.json").read_text(encoding="utf-8"))
    assert rebase_manifest["manual_tombstones"]
    assert "Cartographer" not in (rebased / "master.ged").read_text(encoding="utf-8")

    assert run_sync(_update_args(releases, rebased, _snapshot("ancestry-main", "ancestry", 2))) == 0
    updated = next(releases.glob("g0003-*"))
    assert "Cartographer" not in (updated / "master.ged").read_text(encoding="utf-8")
    assert "intentional manual deletion" in (updated / "update.md").read_text(encoding="utf-8")


def test_update_writes_rollback_metadata_and_cleans_up_interrupted_publish(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)

    assert run_sync(_update_args(releases, first, _snapshot("ancestry-main", "ancestry", 2))) == 0
    second = next(releases.glob("g0002-*"))
    rollback = json.loads((second / "rollback.json").read_text(encoding="utf-8"))
    assert rollback["current_generation"] == 2
    assert rollback["previous"]["generation"] == 1
    assert rollback["previous"]["master"]["path"] == str(first / "master.ged")
    assert (first / "master.ged").is_file()

    with patch("ancestryllm.gedcom.incremental.os.replace", side_effect=OSError("disk full")):
        assert (
            run_sync(_update_args(releases, second, _snapshot("myheritage-main", "myheritage", 2)))
            == 7
        )

    assert sorted(path.name[:5] for path in releases.glob("g*-*")) == ["g0001", "g0002"]
    assert not list(releases.glob(".gedcom-sync-*"))
