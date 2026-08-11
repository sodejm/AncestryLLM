"""Verify genealogy aggregate identity ownership and deterministic safe domain values."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ancestryllm.application.genealogy import GenealogyAggregate
from ancestryllm.application.operations import (
    ChangeSummary,
    ProvenanceRecord,
    QualitySummary,
)
from ancestryllm.domain.genealogy import (
    ChangeKind,
    GenealogyChange,
    GenealogyIdentity,
    GenealogyProvenance,
    GenealogyQualityFinding,
    QualityKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def test_aggregate_owns_identity_and_deterministic_result_accounting() -> None:
    aggregate = GenealogyAggregate(
        identities=(
            GenealogyIdentity("person-b", ("source-z",)),
            GenealogyIdentity("person-a", ("source-y", "source-x", "source-x")),
        ),
        changes=(
            GenealogyChange("person-b", ChangeKind.CONFLICT, ("date-conflict",)),
            GenealogyChange(
                "person-a",
                ChangeKind.CREATED,
                ("source-warning", "name-warning", "name-warning"),
            ),
        ),
        quality_findings=(
            GenealogyQualityFinding("person-b", QualityKind.ERROR, "dangling-family"),
            GenealogyQualityFinding("person-a", QualityKind.WARNING, "date-shape"),
            GenealogyQualityFinding("person-a", QualityKind.INFORMATION, "custom-tag"),
            GenealogyQualityFinding("person-a", QualityKind.RESOLVED, "name-spacing"),
        ),
        provenance=(
            GenealogyProvenance("person-b", ("source-z",), "identity-preserved"),
            GenealogyProvenance(
                "person-a",
                ("source-y", "source-x"),
                "identity-merged",
            ),
        ),
    )

    assert aggregate.canonical_ref_for("source-x") == "person-a"
    assert aggregate.canonical_ref_for("unknown-source") is None
    assert aggregate.change_summary() == ChangeSummary(1, 0, 0, 1, 3)
    assert aggregate.quality_summary() == QualitySummary(1, 1, 1, 1)
    assert aggregate.provenance_records() == (
        ProvenanceRecord(
            result_ref="person-a",
            source_refs=("source-x", "source-y"),
            rule_code="identity-merged",
        ),
        ProvenanceRecord(
            result_ref="person-b",
            source_refs=("source-z",),
            rule_code="identity-preserved",
        ),
    )
    assert aggregate == GenealogyAggregate(
        identities=tuple(reversed(aggregate.identities)),
        changes=tuple(reversed(aggregate.changes)),
        quality_findings=tuple(reversed(aggregate.quality_findings)),
        provenance=tuple(reversed(aggregate.provenance)),
    )


def test_aggregate_rejects_ambiguous_identity_or_change_ownership() -> None:
    with pytest.raises(ValueError, match="multiple canonical identities"):
        GenealogyAggregate(
            identities=(
                GenealogyIdentity("person-a", ("source-x",)),
                GenealogyIdentity("person-b", ("source-x",)),
            )
        )

    with pytest.raises(ValueError, match="one final change"):
        GenealogyAggregate(
            changes=(
                GenealogyChange("person-a", ChangeKind.CREATED),
                GenealogyChange("person-a", ChangeKind.UPDATED),
            )
        )


@pytest.mark.parametrize(
    "factory",
    (
        lambda: GenealogyIdentity("../private/data", ("source-a",)),
        lambda: GenealogyIdentity("person-a", ()),
        lambda: GenealogyChange("person/a", ChangeKind.CREATED),
        lambda: GenealogyQualityFinding("person-a", QualityKind.ERROR, "bad rule"),
        lambda: GenealogyProvenance("person-a", ("source-a",), ""),
    ),
)
def test_domain_values_reject_paths_controls_and_unstable_codes(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(ValueError):
        factory()
