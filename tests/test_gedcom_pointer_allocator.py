"""Deterministic complexity and cancellation tests for GEDCOM xref allocation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ancestryllm.core.cancellation import CancellationError
from ancestryllm.gedcom import parser

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class _CountingSet(set[str]):
    """Count membership probes without relying on machine-dependent timings."""

    def __init__(self, values: Iterator[str]) -> None:
        super().__init__(values)
        self.probes = 0

    def __contains__(self, value: object) -> bool:
        self.probes += 1
        return super().__contains__(value)


@pytest.mark.parametrize("occupied_suffixes", [64, 256, 1024])
def test_suffix_allocation_scales_with_total_work(occupied_suffixes: int) -> None:
    """Dense suffix ranges and repeated collisions consume only linear probes."""

    used = _CountingSet(
        iter(
            {
                "@I1@",
                *(f"@I2_{suffix}@" for suffix in range(1, occupied_suffixes + 1)),
            }
        )
    )
    allocator = parser._PointerAllocator(used, checkpoint=lambda: None)

    allocated = [allocator.allocate("@I1@", 2) for _ in range(occupied_suffixes)]

    assert allocated[0] == f"@I2_{occupied_suffixes + 1}@"
    assert allocated[-1] == f"@I2_{occupied_suffixes * 2}@"
    assert len(allocated) == len(set(allocated))
    assert used.probes <= occupied_suffixes * 3


def test_suffix_allocation_is_deterministic_with_overlapping_identifiers() -> None:
    """Original identifiers that resemble generated suffixes remain unambiguous."""

    originals = ["@I1@", "@I2_1@", "@I1@", "@I2_2@", "@I1@"]

    def allocate() -> list[str]:
        allocator = parser._PointerAllocator(set(), checkpoint=lambda: None)
        return [allocator.allocate(original, 2) for original in originals]

    first = allocate()

    assert first == allocate()
    assert len(first) == len(set(first))
    assert first[:2] == ["@I1@", "@I2_1@"]
    assert first[2] == "@I2_2@"
    assert first[3] != "@I2_2@"


def test_suffix_allocation_checks_cancellation_within_probe_budget() -> None:
    """Dense searches stop within one documented interval and publish nothing."""

    used = _CountingSet(
        iter(
            {
                "@I1@",
                *(
                    f"@I2_{suffix}@"
                    for suffix in range(1, parser._ALLOCATION_PROBES_PER_CHECKPOINT * 4)
                ),
            }
        )
    )
    occupied_before = set(used)
    checkpoint_calls = 0

    def cancel_on_second_checkpoint() -> None:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            raise CancellationError("test cancellation")

    allocator = parser._PointerAllocator(used, checkpoint=cancel_on_second_checkpoint)

    with pytest.raises(CancellationError):
        allocator.allocate("@I1@", 2)

    assert set(used) == occupied_before
    assert used.probes <= parser._ALLOCATION_PROBES_PER_CHECKPOINT + 1


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
