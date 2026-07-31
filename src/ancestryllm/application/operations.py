"""Typed request and result contracts for every current application operation.

These dataclasses are the service surface consumed by terminal adapters today
and by future HTTP or desktop adapters.  Paths, framework models, provider
clients, callbacks, and exception objects are intentionally absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ancestryllm.application.dto import (
    ArtifactGrantRef,
    ArtifactRef,
    BoundaryDTO,
    NamedValue,
    ProviderSelection,
    Scalar,
    SecretGrantRef,
    ServiceRequest,
    ServiceResult,
)
from ancestryllm.core.commands import COMMAND_SPECIFICATIONS, DispatchKey


@dataclass(frozen=True, slots=True)
class ModuleRecord(BoundaryDTO):
    """One configured module without its implementation object."""

    module_id: str
    name: str
    summary: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class TreeRecord(BoundaryDTO):
    """Opaque RootsMagic tree identity and safe display metadata."""

    tree_ref: str
    label: str
    immutable: bool


@dataclass(frozen=True, slots=True)
class RootsMagicSourceSummary(BoundaryDTO):
    """Sanitized metadata with a string fingerprint for one granted source."""

    source_ref: str
    friendly_name: str
    fingerprint: str
    detected_version: str
    grant_status_code: str
    immutable: bool


@dataclass(frozen=True, slots=True)
class RootsMagicQueryParameterDefinition(BoundaryDTO):
    """Transport-neutral validation schema for one allowlisted query parameter."""

    parameter_id: str
    value_type_code: str
    required: bool
    minimum: int | None
    maximum: int | None
    allowed_values: tuple[Scalar, ...]


@dataclass(frozen=True, slots=True)
class RootsMagicQueryDefinition(BoundaryDTO):
    """One allowlisted query definition suitable for adapter presentation."""

    query_id: str
    label: str
    description: str
    parameters: tuple[RootsMagicQueryParameterDefinition, ...]
    maximum_rows: int


@dataclass(frozen=True, slots=True)
class QueryRow(BoundaryDTO):
    """One deterministic query row aligned with result column names."""

    values: tuple[Scalar, ...]


@dataclass(frozen=True, slots=True)
class QueryExecutionRecord(BoundaryDTO):
    """Safe execution metadata for one validated read-only query."""

    mode_code: str
    provider_id: str
    row_limit: int
    returned_rows: int


@dataclass(frozen=True, slots=True)
class ChangeSummary(BoundaryDTO):
    """Deterministic genealogy change and conflict accounting."""

    created: int
    updated: int
    unchanged: int
    conflicts: int
    warnings: int


@dataclass(frozen=True, slots=True)
class QualitySummary(BoundaryDTO):
    """Deterministic quality finding counts."""

    information: int
    warnings: int
    errors: int
    resolved: int


@dataclass(frozen=True, slots=True)
class ProvenanceRecord(BoundaryDTO):
    """Coded provenance link between opaque source and result identities."""

    result_ref: str
    source_refs: tuple[str, ...]
    rule_code: str


@dataclass(frozen=True, slots=True)
class PromptRecord(BoundaryDTO):
    """Versioned prompt metadata without provider or filesystem objects."""

    name: str
    version: int
    purpose: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PersonRecord(BoundaryDTO):
    """Current person fields returned by the application service."""

    person_ref: str
    display_name: str
    living_status: str
    notes: str | None
    workspace_ref: str


@dataclass(frozen=True, slots=True)
class ProviderProfileRecord(BoundaryDTO):
    """Provider profile metadata with secrets excluded."""

    profile_id: str
    provider_id: str
    model_id: str
    settings: tuple[NamedValue, ...]


@dataclass(frozen=True, slots=True)
class ConsentRecord(BoundaryDTO):
    """Explicit cloud-processing consent expressed with stable values."""

    consent_id: str
    profile_id: str
    modules: tuple[str, ...]
    purposes: tuple[str, ...]
    data_classes: tuple[str, ...]
    models: tuple[str, ...]
    max_cost_usd: str | None
    retain_payloads: bool
    revoked: bool


@dataclass(frozen=True, slots=True)
class SecretStatusRecord(BoundaryDTO):
    """Presence-only secret status; secret values never cross the boundary."""

    secret_name: str
    present: bool
    storage_code: str


@dataclass(frozen=True, slots=True)
class OcrRecord(BoundaryDTO):
    """One OCR record represented without a provider SDK model."""

    record_type: str
    fields: tuple[NamedValue, ...]


@dataclass(frozen=True, slots=True)
class DiagnosticRecord(BoundaryDTO):
    """One coded diagnostic outcome without host paths or exception text."""

    check_code: str
    status_code: str
    detail_code: str | None = None


@dataclass(frozen=True, slots=True)
class ModulesListRequest(ServiceRequest):
    """List configured module descriptors."""


@dataclass(frozen=True, slots=True)
class ModulesListResult(ServiceResult):
    """Configured module descriptors in deterministic order."""

    modules: tuple[ModuleRecord, ...]


@dataclass(frozen=True, slots=True)
class ModuleEnableRequest(ServiceRequest):
    """Enable one declared module."""

    module_id: str


@dataclass(frozen=True, slots=True)
class ModuleEnableResult(ServiceResult):
    """Resulting module state."""

    module: ModuleRecord
    changed: bool


@dataclass(frozen=True, slots=True)
class ModuleDisableRequest(ServiceRequest):
    """Disable one declared module."""

    module_id: str


@dataclass(frozen=True, slots=True)
class ModuleDisableResult(ServiceResult):
    """Resulting module state."""

    module: ModuleRecord
    changed: bool


@dataclass(frozen=True, slots=True)
class RootsMagicListRequest(ServiceRequest):
    """Discover configured immutable RootsMagic trees."""


@dataclass(frozen=True, slots=True)
class RootsMagicListResult(ServiceResult):
    """Configured trees represented by adapter-issued opaque references."""

    trees: tuple[TreeRecord, ...]


@dataclass(frozen=True, slots=True)
class RootsMagicQueryRequest(ServiceRequest):
    """Run SQL or a provider-assisted question against one immutable tree."""

    tree_ref: str
    sql: str | None
    question: str | None
    provider: ProviderSelection


@dataclass(frozen=True, slots=True)
class RootsMagicQueryResult(ServiceResult):
    """Serializable query rows with stable ordering."""

    columns: tuple[str, ...]
    rows: tuple[QueryRow, ...]
    truncated: bool
    execution: QueryExecutionRecord


@dataclass(frozen=True, slots=True)
class RootsMagicResultPage(BoundaryDTO):
    """One bounded page of sanitized RootsMagic query results."""

    query_id: str
    columns: tuple[str, ...]
    rows: tuple[QueryRow, ...]
    offset: int
    returned_rows: int
    total_rows: int | None
    has_more: bool
    next_offset: int | None


@dataclass(frozen=True, slots=True)
class RootsMagicExportRequest(ServiceRequest):
    """Export an immutable RootsMagic tree through scoped output grants."""

    tree_ref: str
    output: ArtifactGrantRef
    report: ArtifactGrantRef
    profile: str
    gedcom_version: str
    destination: str
    root_person_ref: str | None
    scope: str
    generations: int | None
    living: str


@dataclass(frozen=True, slots=True)
class RootsMagicExportResult(ServiceResult):
    """Published GEDCOM and report with deterministic accounting."""

    gedcom: ArtifactRef
    report: ArtifactRef
    changes: ChangeSummary
    quality: QualitySummary
    provenance: tuple[ProvenanceRecord, ...]


@dataclass(frozen=True, slots=True)
class RootsMagicExportArtifact(BoundaryDTO):
    """Published metadata with a sanitized string fingerprint and no host path."""

    artifact: ArtifactRef
    source_ref: str
    source_fingerprint: str
    profile_code: str
    gedcom_version: str


@dataclass(frozen=True, slots=True)
class GedcomMergeRequest(ServiceRequest):
    """Merge granted GEDCOM inputs into granted loss-minimal outputs."""

    inputs: tuple[ArtifactGrantRef, ...]
    output: ArtifactGrantRef
    quality_report: ArtifactGrantRef
    root_person_ref: str | None
    gedcom_version: str
    provider: ProviderSelection
    similarity_threshold: int


@dataclass(frozen=True, slots=True)
class GedcomMergeResult(ServiceResult):
    """Published merge artifacts and deterministic result contracts."""

    gedcom: ArtifactRef
    quality_report: ArtifactRef
    root_person_ref: str
    changes: ChangeSummary
    quality: QualitySummary
    provenance: tuple[ProvenanceRecord, ...]


@dataclass(frozen=True, slots=True)
class GedcomSubtreeRequest(ServiceRequest):
    """Extract a rooted GEDCOM subtree through scoped artifact grants."""

    source: ArtifactGrantRef
    output: ArtifactGrantRef
    root_person_ref: str
    scope: str
    generations: int | None
    gedcom_version: str


@dataclass(frozen=True, slots=True)
class GedcomSubtreeResult(ServiceResult):
    """Published rooted subtree and deterministic accounting."""

    gedcom: ArtifactRef
    root_person_ref: str
    changes: ChangeSummary
    provenance: tuple[ProvenanceRecord, ...]


@dataclass(frozen=True, slots=True)
class GedcomQualityRequest(ServiceRequest):
    """Analyze one granted GEDCOM and optionally publish a report."""

    source: ArtifactGrantRef
    output: ArtifactGrantRef
    root_person_ref: str | None
    provider: ProviderSelection


@dataclass(frozen=True, slots=True)
class GedcomQualityResult(ServiceResult):
    """Published quality report and deterministic finding counts."""

    report: ArtifactRef
    quality: QualitySummary


@dataclass(frozen=True, slots=True)
class GedcomSyncSnapshot(BoundaryDTO):
    """One website snapshot identified without exposing its host path."""

    source_id: str
    vendor: str
    artifact: ArtifactGrantRef
    exported_at: str | None = None


@dataclass(frozen=True, slots=True)
class GedcomSyncRequest(ServiceRequest):
    """Run one typed sync operation with scoped artifact grants."""

    sync_command: str
    master: ArtifactGrantRef
    release_root: ArtifactGrantRef
    provider: ProviderSelection
    manifest: ArtifactGrantRef | None = None
    snapshots: tuple[GedcomSyncSnapshot, ...] = ()
    initialize_manifest: bool = False
    quality_root_person_ref: str | None = None
    quality_report_enabled: bool = True
    dry_run: bool = False
    accept_manual_deletions: bool = False
    reason: str | None = None
    gedcom_version: str = "5.5.5"
    automatic_identity_resolution: bool = True


@dataclass(frozen=True, slots=True)
class GedcomSyncResult(ServiceResult):
    """Sync artifacts and deterministic change/conflict accounting."""

    committed: bool
    artifacts: tuple[ArtifactRef, ...]
    changes: ChangeSummary
    quality: QualitySummary
    provenance: tuple[ProvenanceRecord, ...]


@dataclass(frozen=True, slots=True)
class PromptsListRequest(ServiceRequest):
    """List versioned prompts."""


@dataclass(frozen=True, slots=True)
class PromptsListResult(ServiceResult):
    """Prompt metadata in deterministic order."""

    prompts: tuple[PromptRecord, ...]


@dataclass(frozen=True, slots=True)
class PromptSaveRequest(ServiceRequest):
    """Save one prompt version from inline text or a granted artifact."""

    name: str
    purpose: str
    body: str | None
    body_artifact: ArtifactGrantRef | None
    variables: tuple[str, ...]
    schema_artifact: ArtifactGrantRef | None
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PromptSaveResult(ServiceResult):
    """Saved prompt metadata."""

    prompt: PromptRecord
    created: bool


@dataclass(frozen=True, slots=True)
class PromptShowRequest(ServiceRequest):
    """Load one prompt version."""

    name: str
    version: int | None


@dataclass(frozen=True, slots=True)
class PromptShowResult(ServiceResult):
    """Prompt definition without a host filesystem representation."""

    prompt: PromptRecord
    body: str
    variables: tuple[str, ...]
    schema_json: str | None


@dataclass(frozen=True, slots=True)
class PromptRenderRequest(ServiceRequest):
    """Render a prompt with a deterministic tuple of values."""

    name: str
    version: int | None
    values: tuple[NamedValue, ...]


@dataclass(frozen=True, slots=True)
class PromptRenderResult(ServiceResult):
    """Rendered prompt content."""

    rendered_text: str
    prompt: PromptRecord


@dataclass(frozen=True, slots=True)
class PeopleListRequest(ServiceRequest):
    """List people in one application workspace."""

    workspace_ref: str


@dataclass(frozen=True, slots=True)
class PeopleListResult(ServiceResult):
    """People in deterministic order."""

    people: tuple[PersonRecord, ...]


@dataclass(frozen=True, slots=True)
class PersonAddRequest(ServiceRequest):
    """Create one person under service-owned identity rules."""

    display_name: str
    living_status: str
    notes: str | None
    workspace_ref: str


@dataclass(frozen=True, slots=True)
class PersonAddResult(ServiceResult):
    """Created person with its service-owned opaque identity."""

    person: PersonRecord
    changes: ChangeSummary
    provenance: tuple[ProvenanceRecord, ...]


@dataclass(frozen=True, slots=True)
class ProvidersListRequest(ServiceRequest):
    """List provider profiles and consent grants."""


@dataclass(frozen=True, slots=True)
class ProvidersListResult(ServiceResult):
    """Provider metadata without credentials or SDK objects."""

    profiles: tuple[ProviderProfileRecord, ...]
    consents: tuple[ConsentRecord, ...]


@dataclass(frozen=True, slots=True)
class ProviderCreateRequest(ServiceRequest):
    """Create one provider profile without embedding a credential."""

    name: str
    provider_id: str
    model_id: str
    settings: tuple[NamedValue, ...]


@dataclass(frozen=True, slots=True)
class ProviderCreateResult(ServiceResult):
    """Created provider profile."""

    profile: ProviderProfileRecord


@dataclass(frozen=True, slots=True)
class ProviderConsentRequest(ServiceRequest):
    """Create an explicit, bounded cloud-processing consent grant."""

    name: str
    profile_id: str
    modules: tuple[str, ...]
    purposes: tuple[str, ...]
    data_classes: tuple[str, ...]
    models: tuple[str, ...]
    max_cost_usd: str | None
    retain_payloads: bool


@dataclass(frozen=True, slots=True)
class ProviderConsentResult(ServiceResult):
    """Created consent grant."""

    consent: ConsentRecord


@dataclass(frozen=True, slots=True)
class ProviderRevokeRequest(ServiceRequest):
    """Revoke one consent grant."""

    consent_id: str


@dataclass(frozen=True, slots=True)
class ProviderRevokeResult(ServiceResult):
    """Resulting consent state."""

    consent: ConsentRecord
    changed: bool


@dataclass(frozen=True, slots=True)
class SecretSetRequest(ServiceRequest):
    """Store a secret through a write-only adapter-issued capability."""

    secret_name: str
    secret: SecretGrantRef


@dataclass(frozen=True, slots=True)
class SecretSetResult(ServiceResult):
    """Presence-only secret status."""

    status: SecretStatusRecord


@dataclass(frozen=True, slots=True)
class SecretDeleteRequest(ServiceRequest):
    """Delete one named secret."""

    secret_name: str


@dataclass(frozen=True, slots=True)
class SecretDeleteResult(ServiceResult):
    """Presence-only secret status after deletion."""

    status: SecretStatusRecord
    changed: bool


@dataclass(frozen=True, slots=True)
class SecretStatusRequest(ServiceRequest):
    """Inspect presence of one named secret."""

    secret_name: str


@dataclass(frozen=True, slots=True)
class SecretStatusResult(ServiceResult):
    """Presence-only secret status."""

    status: SecretStatusRecord


@dataclass(frozen=True, slots=True)
class OcrExtractRequest(ServiceRequest):
    """Extract records from one granted image artifact."""

    source: ArtifactGrantRef
    provider: ProviderSelection


@dataclass(frozen=True, slots=True)
class OcrExtractResult(ServiceResult):
    """Provider-neutral OCR records."""

    records: tuple[OcrRecord, ...]


@dataclass(frozen=True, slots=True)
class DatabaseBackupRequest(ServiceRequest):
    """Create a database backup at one granted output."""

    destination: ArtifactGrantRef


@dataclass(frozen=True, slots=True)
class DatabaseBackupResult(ServiceResult):
    """Published database backup artifact."""

    backup: ArtifactRef


@dataclass(frozen=True, slots=True)
class DatabaseDiagnoseRequest(ServiceRequest):
    """Run safe application database diagnostics."""


@dataclass(frozen=True, slots=True)
class DatabaseDiagnoseResult(ServiceResult):
    """Coded diagnostics without host paths or raw exception text."""

    checks: tuple[DiagnosticRecord, ...]


@dataclass(frozen=True, slots=True)
class OperationContract:
    """Stable dispatch identity paired with exact request and result types."""

    key: DispatchKey
    request_type: type[ServiceRequest]
    result_type: type[ServiceResult]


_CONTRACT_TYPES = (
    ("modules", "list", ModulesListRequest, ModulesListResult),
    ("modules", "enable", ModuleEnableRequest, ModuleEnableResult),
    ("modules", "disable", ModuleDisableRequest, ModuleDisableResult),
    ("rootsmagic", "list", RootsMagicListRequest, RootsMagicListResult),
    ("rootsmagic", "query", RootsMagicQueryRequest, RootsMagicQueryResult),
    ("rootsmagic", "export", RootsMagicExportRequest, RootsMagicExportResult),
    ("gedcom", "merge", GedcomMergeRequest, GedcomMergeResult),
    ("gedcom", "subtree", GedcomSubtreeRequest, GedcomSubtreeResult),
    ("gedcom", "quality", GedcomQualityRequest, GedcomQualityResult),
    ("gedcom", "sync", GedcomSyncRequest, GedcomSyncResult),
    ("prompts", "list", PromptsListRequest, PromptsListResult),
    ("prompts", "save", PromptSaveRequest, PromptSaveResult),
    ("prompts", "show", PromptShowRequest, PromptShowResult),
    ("prompts", "render", PromptRenderRequest, PromptRenderResult),
    ("people", "list", PeopleListRequest, PeopleListResult),
    ("people", "add", PersonAddRequest, PersonAddResult),
    ("providers", "list", ProvidersListRequest, ProvidersListResult),
    ("providers", "create", ProviderCreateRequest, ProviderCreateResult),
    ("providers", "consent", ProviderConsentRequest, ProviderConsentResult),
    ("providers", "revoke", ProviderRevokeRequest, ProviderRevokeResult),
    ("secrets", "set", SecretSetRequest, SecretSetResult),
    ("secrets", "delete", SecretDeleteRequest, SecretDeleteResult),
    ("secrets", "status", SecretStatusRequest, SecretStatusResult),
    ("ocr", "extract", OcrExtractRequest, OcrExtractResult),
    ("database", "backup", DatabaseBackupRequest, DatabaseBackupResult),
    ("database", "diagnose", DatabaseDiagnoseRequest, DatabaseDiagnoseResult),
)

OPERATION_CONTRACTS: Mapping[DispatchKey, OperationContract] = MappingProxyType(
    {
        DispatchKey(command, action): OperationContract(
            DispatchKey(command, action),
            request_type,
            result_type,
        )
        for command, action, request_type, result_type in _CONTRACT_TYPES
    }
)

_COMMAND_KEYS = {
    route.key for specification in COMMAND_SPECIFICATIONS.values() for route in specification.routes
}
if set(OPERATION_CONTRACTS) != _COMMAND_KEYS:
    missing = sorted(key.value for key in _COMMAND_KEYS - set(OPERATION_CONTRACTS))
    extra = sorted(key.value for key in set(OPERATION_CONTRACTS) - _COMMAND_KEYS)
    raise RuntimeError(f"Application operation contract drift: missing={missing}, extra={extra}")


__all__ = [
    "OPERATION_CONTRACTS",
    "ChangeSummary",
    "ConsentRecord",
    "DatabaseBackupRequest",
    "DatabaseBackupResult",
    "DatabaseDiagnoseRequest",
    "DatabaseDiagnoseResult",
    "DiagnosticRecord",
    "GedcomMergeRequest",
    "GedcomMergeResult",
    "GedcomQualityRequest",
    "GedcomQualityResult",
    "GedcomSubtreeRequest",
    "GedcomSubtreeResult",
    "GedcomSyncRequest",
    "GedcomSyncResult",
    "GedcomSyncSnapshot",
    "ModuleDisableRequest",
    "ModuleDisableResult",
    "ModuleEnableRequest",
    "ModuleEnableResult",
    "ModuleRecord",
    "ModulesListRequest",
    "ModulesListResult",
    "OcrExtractRequest",
    "OcrExtractResult",
    "OcrRecord",
    "OperationContract",
    "PeopleListRequest",
    "PeopleListResult",
    "PersonAddRequest",
    "PersonAddResult",
    "PersonRecord",
    "PromptRecord",
    "PromptRenderRequest",
    "PromptRenderResult",
    "PromptSaveRequest",
    "PromptSaveResult",
    "PromptShowRequest",
    "PromptShowResult",
    "PromptsListRequest",
    "PromptsListResult",
    "ProvenanceRecord",
    "ProviderConsentRequest",
    "ProviderConsentResult",
    "ProviderCreateRequest",
    "ProviderCreateResult",
    "ProviderProfileRecord",
    "ProviderRevokeRequest",
    "ProviderRevokeResult",
    "ProvidersListRequest",
    "ProvidersListResult",
    "QualitySummary",
    "QueryExecutionRecord",
    "QueryRow",
    "RootsMagicExportArtifact",
    "RootsMagicExportRequest",
    "RootsMagicExportResult",
    "RootsMagicListRequest",
    "RootsMagicListResult",
    "RootsMagicQueryDefinition",
    "RootsMagicQueryParameterDefinition",
    "RootsMagicQueryRequest",
    "RootsMagicQueryResult",
    "RootsMagicResultPage",
    "RootsMagicSourceSummary",
    "SecretDeleteRequest",
    "SecretDeleteResult",
    "SecretSetRequest",
    "SecretSetResult",
    "SecretStatusRecord",
    "SecretStatusRequest",
    "SecretStatusResult",
    "TreeRecord",
]
