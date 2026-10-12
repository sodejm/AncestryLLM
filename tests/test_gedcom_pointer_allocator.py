"""Deterministic complexity and cancellation tests for GEDCOM xref allocation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ancestryllm.core.cancellation import CancellationError
from ancestryllm.gedcom import parser

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class _CountingSet(set[str]):
    """Count membership probes without relying on machine-dependent timings."""

    def __init__(self, values: Iterable[str]) -> None:
        super().__init__(values)
        self.probes = 0
        self.added: list[str] = []
        self.collision_started = False
        self.probes_after_collision = 0
        self.occupied_at_collision: frozenset[str] | None = None

    def __contains__(self, value: object) -> bool:
        self.probes += 1
        occupied = super().__contains__(value)
        if value == "@I1@" and occupied:
            self.collision_started = True
            self.probes_after_collision = 0
            self.occupied_at_collision = frozenset(self)
        elif self.collision_started:
            self.probes_after_collision += 1
        return occupied

    def add(self, value: str) -> None:
        self.added.append(value)
        super().add(value)


def _write_allocator_inputs(
    tmp_path: Path,
    *,
    occupied_suffixes: int,
    duplicate_allocations: int,
) -> tuple[Path, Path]:
    """Write public-parser inputs that exercise one dense collision namespace."""
    first_path = tmp_path / "first.ged"
    second_path = tmp_path / "second.ged"
    first_path.write_text(
        "\n".join(
            [
                "0 HEAD",
                "0 @I1@ INDI",
                *(f"0 @I2_{suffix}@ INDI" for suffix in range(1, occupied_suffixes + 1)),
                "0 TRLR",
            ]
        ),
        encoding="utf-8",
    )
    second_path.write_text(
        "\n".join(
            [
                "0 HEAD",
                *("0 @I1@ INDI" for _ in range(duplicate_allocations)),
                "0 TRLR",
            ]
        ),
        encoding="utf-8",
    )
    return first_path, second_path


def _observe_allocator_set(monkeypatch: pytest.MonkeyPatch) -> list[_CountingSet]:
    """Substitute the allocator's collection through the exported loader seam."""
    observed: list[_CountingSet] = []

    def counting_set(values: Iterable[str] | None = None) -> _CountingSet:
        instance = _CountingSet(() if values is None else values)
        observed.append(instance)
        return instance

    monkeypatch.setitem(parser.load_sources.__globals__, "set", counting_set)
    return observed


@pytest.mark.parametrize("occupied_suffixes", [64, 256, 1024])
def test_suffix_allocation_scales_with_total_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    occupied_suffixes: int,
) -> None:
    """Dense suffix ranges and repeated collisions consume only linear probes."""

    paths = _write_allocator_inputs(
        tmp_path,
        occupied_suffixes=occupied_suffixes,
        duplicate_allocations=occupied_suffixes,
    )
    observed = _observe_allocator_set(monkeypatch)

    parser.load_sources(paths)

    used = observed[0]
    allocated = [value for value in used.added if value.startswith("@I2_")][occupied_suffixes:]
    assert allocated[0] == f"@I2_{occupied_suffixes + 1}@"
    assert allocated[-1] == f"@I2_{occupied_suffixes * 2}@"
    assert len(allocated) == len(set(allocated))
    assert used.probes <= occupied_suffixes * 4 + 1


def test_suffix_allocation_is_deterministic_with_overlapping_identifiers(
    tmp_path: Path,
) -> None:
    """Original identifiers that resemble generated suffixes remain unambiguous."""

    first_path = tmp_path / "first.ged"
    second_path = tmp_path / "second.ged"
    first_path.write_text("0 HEAD\n0 @I1@ INDI\n0 TRLR\n", encoding="utf-8")
    second_path.write_text(
        "0 HEAD\n0 @I2_1@ INDI\n0 @I1@ INDI\n0 @I2_2@ INDI\n0 TRLR\n",
        encoding="utf-8",
    )

    first = parser.load_sources([first_path, second_path])
    repeated = parser.load_sources([first_path, second_path])

    assert [source.pointer_map for source in first] == [source.pointer_map for source in repeated]
    allocated = list(first[1].pointer_map.values())
    assert len(allocated) == len(set(allocated))
    assert first[0].pointer_map["@I1@"] == "@I1@"
    assert first[1].pointer_map["@I2_1@"] == "@I2_1@"
    assert first[1].pointer_map["@I1@"] == "@I2_2@"
    assert first[1].pointer_map["@I2_2@"] != "@I2_2@"


def test_suffix_allocation_checks_cancellation_within_probe_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dense searches stop within one documented interval and publish nothing."""

    probe_budget = 64
    paths = _write_allocator_inputs(
        tmp_path,
        occupied_suffixes=probe_budget * 4,
        duplicate_allocations=1,
    )
    observed = _observe_allocator_set(monkeypatch)

    def cancel_after_budget() -> None:
        if observed and observed[0].probes_after_collision >= probe_budget:
            raise CancellationError("test cancellation")

    monkeypatch.setitem(
        parser.load_sources.__globals__,
        "cancellation_checkpoint",
        cancel_after_budget,
    )

    with pytest.raises(CancellationError):
        parser.load_sources(paths)

    used = observed[0]
    assert used.occupied_at_collision is not None
    assert frozenset(used) == used.occupied_at_collision
    assert used.probes_after_collision <= probe_budget


def test_load_sources_rewrites_references_after_dense_suffix_collisions(tmp_path: Path) -> None:
    """A renamed record and its references resolve after a dense collision range."""

    occupied_suffixes = 128
    first_path = tmp_path / "first.ged"
    second_path = tmp_path / "second.ged"
    first_path.write_text(
        "\n".join(
            [
                "0 HEAD",
                "0 @I1@ INDI",
                *(f"0 @I2_{suffix}@ INDI" for suffix in range(1, occupied_suffixes + 1)),
                "0 TRLR",
            ]
        ),
        encoding="utf-8",
    )
    second_path.write_text(
        "\n".join(
            [
                "0 HEAD",
                "0 @I1@ INDI",
                "1 FAMS @F1@",
                "0 @F1@ FAM",
                "1 HUSB @I1@",
                "0 TRLR",
            ]
        ),
        encoding="utf-8",
    )

    first, second = parser.load_sources([first_path, second_path])

    allocated = second.pointer_map["@I1@"]
    assert allocated == f"@I2_{occupied_suffixes + 1}@"
    assert allocated not in first.pointer_map.values()
    rewritten_lines = {line for record in second.records for line in record.lines}
    assert f"0 {allocated} INDI" in rewritten_lines
    assert f"1 HUSB {allocated}" in rewritten_lines
    assert "1 FAMS @F1@" in rewritten_lines
