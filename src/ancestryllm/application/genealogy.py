"""Service-owned genealogy aggregate and deterministic result accounting."""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class GenealogyAggregate:
    """Canonical genealogy state used to produce transport-neutral results.

    The aggregate rejects ambiguous source identities and multiple final change
    dispositions for the same subject. Inputs are canonicalized so results are
    independent of adapter, provider, filesystem, and traversal order.
    """

    identities: tuple[GenealogyIdentity, ...] = ()
    changes: tuple[GenealogyChange, ...] = ()
    quality_findings: tuple[GenealogyQualityFinding, ...] = ()
    provenance: tuple[GenealogyProvenance, ...] = ()

    def __post_init__(self) -> None:
        identities = tuple(
            sorted(
                set(self.identities),
                key=lambda identity: (identity.canonical_ref, identity.source_refs),
            )
        )
        canonical_refs: set[str] = set()
        source_owners: dict[str, str] = {}
        for identity in identities:
            if identity.canonical_ref in canonical_refs:
                raise ValueError("canonical_ref must identify exactly one identity.")
            canonical_refs.add(identity.canonical_ref)
            for source_ref in identity.source_refs:
                owner = source_owners.setdefault(source_ref, identity.canonical_ref)
                if owner != identity.canonical_ref:
                    raise ValueError("source_ref cannot map to multiple canonical identities.")

        changes = tuple(
            sorted(
                set(self.changes),
                key=lambda change: (
                    change.subject_ref,
                    change.kind.value,
                    change.warning_codes,
                ),
            )
        )
        changed_subjects: set[str] = set()
        for change in changes:
            if change.subject_ref in changed_subjects:
                raise ValueError("subject_ref must have exactly one final change disposition.")
            changed_subjects.add(change.subject_ref)

        quality_findings = tuple(
            sorted(
                set(self.quality_findings),
                key=lambda finding: (
                    finding.subject_ref,
                    finding.rule_code,
                    finding.kind.value,
                ),
            )
        )
        provenance = tuple(
            sorted(
                set(self.provenance),
                key=lambda record: (
                    record.result_ref,
                    record.rule_code,
                    record.source_refs,
                ),
            )
        )

        object.__setattr__(self, "identities", identities)
        object.__setattr__(self, "changes", changes)
        object.__setattr__(self, "quality_findings", quality_findings)
        object.__setattr__(self, "provenance", provenance)

    def canonical_ref_for(self, source_ref: str) -> str | None:
        """Return the unique canonical identity for a source reference."""

        return next(
            (
                identity.canonical_ref
                for identity in self.identities
                if source_ref in identity.source_refs
            ),
            None,
        )

    def change_summary(self) -> ChangeSummary:
        """Return deterministic created/updated/unchanged/conflict accounting."""

        counts = {kind: 0 for kind in ChangeKind}
        warnings = 0
        for change in self.changes:
            counts[change.kind] += 1
            warnings += len(change.warning_codes)
        return ChangeSummary(
            created=counts[ChangeKind.CREATED],
            updated=counts[ChangeKind.UPDATED],
            unchanged=counts[ChangeKind.UNCHANGED],
            conflicts=counts[ChangeKind.CONFLICT],
            warnings=warnings,
        )

    def quality_summary(self) -> QualitySummary:
        """Return deterministic quality finding counts."""

        counts = {kind: 0 for kind in QualityKind}
        for finding in self.quality_findings:
            counts[finding.kind] += 1
        return QualitySummary(
            information=counts[QualityKind.INFORMATION],
            warnings=counts[QualityKind.WARNING],
            errors=counts[QualityKind.ERROR],
            resolved=counts[QualityKind.RESOLVED],
        )

    def provenance_records(self) -> tuple[ProvenanceRecord, ...]:
        """Return stable transport records without exposing domain objects."""

        return tuple(
            ProvenanceRecord(
                result_ref=record.result_ref,
                source_refs=record.source_refs,
                rule_code=record.rule_code,
            )
            for record in self.provenance
        )


__all__ = ["GenealogyAggregate"]
