"""Deterministic complexity and cancellation tests for GEDCOM xref allocation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ancestryllm.gedcom import parser

if TYPE_CHECKING:
    from collections.abc import Iterator


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
