"""Verify transport-neutral application DTO, operation, artifact, and error contracts."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import types
from dataclasses import MISSING, fields
from enum import StrEnum
from pathlib import Path
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

import pytest

from ancestryllm.application import dto as dto_module
from ancestryllm.application import operations as operations_module
from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application._compat import (
    _CurrentCancellationAdapter,
    _CurrentProgressAdapter,
)
from ancestryllm.application.dto import (
    CONTRACT_VERSION,
    ArtifactAccess,
    ArtifactGrantRef,
    ArtifactRef,
    ArtifactStatus,
    BoundaryDTO,
    FailureDetail,
    IdentityResolutionResult,
    ProgressUpdate,
    ProviderSelection,
    QualityResolutionResult,
    SecretGrantRef,
)
from ancestryllm.application.errors import (
    DOMAIN_ERROR_MAPPINGS,
    domain_failure_from_exception,
    error_envelope,
    map_domain_failure,
)
from ancestryllm.application.operations import OPERATION_CONTRACTS
from ancestryllm.application.ports import (
    CancellationPort,
    DecisionPort,
    DiscardProgress,
    IdentityResolutionPort,
    NeverCancelled,
    ProgressPort,
    QualityResolutionPort,
)
from ancestryllm.core.cancellation import CancellationError
from ancestryllm.core.commands import COMMAND_SPECIFICATIONS
from ancestryllm.core.errors import AncestryError, FileIngressError, ProviderError
from ancestryllm.domain.errors import (
    DomainFailure,
    DomainFailureCode,
    DomainFailureDetail,
)


def _sample(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin is types.UnionType or origin is Union:
        options = get_args(annotation)
        if type(None) in options:
            return None
        return _sample(options[0])
    if origin is tuple:
        item_type, variable = get_args(annotation)
        assert variable is Ellipsis
        return (_sample(item_type),)
    if annotation is ArtifactGrantRef:
        return ArtifactGrantRef(
            f"grt_{'a' * 64}",
            "gedcom.merge",
            ArtifactAccess.READ,
        )
    if annotation is ArtifactRef:
        return ArtifactRef(
            f"art_{'b' * 64}",
            "application/octet-stream",
            "fixture",
            1,
            ArtifactStatus.READY,
            "c" * 64,
        )
    if annotation is SecretGrantRef:
        return SecretGrantRef(f"sec_{'d' * 64}", "fixture")
    if annotation is IdentityResolutionResult:
        return IdentityResolutionResult("resolution-1", selected_ref="person-1")
    if annotation is QualityResolutionResult:
        return QualityResolutionResult("resolution-1", "accept")
    if isinstance(annotation, type) and issubclass(annotation, BoundaryDTO):
        hints = get_type_hints(annotation)
        values: dict[str, object] = {}
        for field in fields(cast(Any, annotation)):
            if field.default is not MISSING or field.default_factory is not MISSING:
                continue
            values[field.name] = _sample(hints[field.name])
        return annotation(**values)
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return next(iter(annotation))
    if annotation is str:
        return "value"
    if annotation is bool:
        return False
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is type(None):
        return None
    raise AssertionError(f"unsupported test annotation: {annotation!r}")


def _boundary_types() -> tuple[type[BoundaryDTO], ...]:
    discovered: set[type[BoundaryDTO]] = set()
    for module in (dto_module, operations_module):
        for _, candidate in inspect.getmembers(module, inspect.isclass):
            if issubclass(candidate, BoundaryDTO) and hasattr(
                candidate,
                "__dataclass_fields__",
            ):
                discovered.add(candidate)
    return tuple(sorted(discovered, key=lambda candidate: candidate.__name__))


def _annotation_leaves(annotation: object) -> tuple[object, ...]:
    origin = get_origin(annotation)
    if origin is types.UnionType or origin is Union or origin is tuple:
        leaves: list[object] = []
        for argument in get_args(annotation):
            if argument is Ellipsis:
                continue
            leaves.extend(_annotation_leaves(argument))
        return tuple(leaves)
    return (annotation,)


def test_operation_contracts_cover_every_command_route_exactly() -> None:
    command_keys = {
        route.key
        for specification in COMMAND_SPECIFICATIONS.values()
        for route in specification.routes
    }

    assert set(OPERATION_CONTRACTS) == command_keys
    assert len(OPERATION_CONTRACTS) == 26
    assert all(contract.key == key for key, contract in OPERATION_CONTRACTS.items())


def test_rootsmagic_workbench_dtos_are_public_stable_and_transport_neutral() -> None:
    public_names = {
        "RootsMagicExportArtifact",
        "RootsMagicQueryDefinition",
        "RootsMagicQueryRequest",
        "RootsMagicResultPage",
        "RootsMagicSourceSummary",
    }
    assert public_names <= set(operations_module.__all__)

    parameter = operations_module.RootsMagicQueryParameterDefinition(
        parameter_id="minimum_birth_year",
        value_type_code="integer",
        required=False,
        minimum=1,
        maximum=9999,
        allowed_values=(),
    )
    values: tuple[BoundaryDTO, ...] = (
        operations_module.RootsMagicSourceSummary(
            source_ref="grant_rm_fixture",
            friendly_name="Fixture tree",
            fingerprint="a" * 64,
            detected_version="10",
            grant_status_code="ready",
            immutable=True,
        ),
        operations_module.RootsMagicQueryDefinition(
            query_id="people_by_birth_year",
            label="People by birth year",
            description="Returns a bounded set of matching people.",
            parameters=(parameter,),
            maximum_rows=100,
        ),
        operations_module.RootsMagicResultPage(
            query_id="people_by_birth_year",
            columns=("person_ref", "display_name"),
            rows=(operations_module.QueryRow(("person_1", "Ada Example")),),
            offset=0,
            returned_rows=1,
            total_rows=None,
            has_more=False,
            next_offset=None,
        ),
        operations_module.RootsMagicExportArtifact(
            artifact=cast(ArtifactRef, _sample(ArtifactRef)),
            source_ref="grant_rm_fixture",
            source_fingerprint="a" * 64,
            profile_code="portable",
            gedcom_version="5.5.5",
        ),
    )

    for value in values:
        encoded = value.to_json()
        assert type(value).from_json(encoded) == value
        assert "/Users/" not in encoded
        assert "C:\\\\" not in encoded
        assert "SourceFingerprint" not in encoded

    source_summary = cast(operations_module.RootsMagicSourceSummary, values[0])
    export_artifact = cast(operations_module.RootsMagicExportArtifact, values[3])
    assert type(source_summary.fingerprint) is str
    assert type(export_artifact.source_fingerprint) is str
    assert get_type_hints(operations_module.RootsMagicSourceSummary)["fingerprint"] is str
    assert get_type_hints(operations_module.RootsMagicExportArtifact)["source_fingerprint"] is str
    assert all(
        not isinstance(getattr(source_summary, field.name), Path)
        for field in fields(source_summary)
    )
    assert all(
        not isinstance(getattr(export_artifact, field.name), Path)
        for field in fields(export_artifact)
    )

    assert tuple(field.name for field in fields(operations_module.RootsMagicQueryRequest)) == (
        "tree_ref",
        "sql",
        "question",
        "provider",
    )


@pytest.mark.parametrize(
    "operation",
    tuple(OPERATION_CONTRACTS.values()),
    ids=lambda contract: contract.key.value,
)
def test_every_operation_request_and_result_has_canonical_json(
    operation: operations_module.OperationContract,
) -> None:
    for contract_type in (operation.request_type, operation.result_type):
        value = _sample(contract_type)
        assert isinstance(value, contract_type)

        encoded = value.to_json()
        decoded = contract_type.from_json(encoded)

        assert decoded == value
        assert decoded.to_json() == encoded
        assert set(json.loads(encoded)) == {"contract", "type", "value"}


@pytest.mark.parametrize("contract_type", _boundary_types(), ids=lambda value: value.__name__)
def test_boundary_types_are_frozen_slotted_and_use_only_safe_annotations(
    contract_type: type[BoundaryDTO],
) -> None:
    assert cast(Any, contract_type).__dataclass_params__.frozen
    assert "__slots__" in contract_type.__dict__

    forbidden_types = {
        Path,
        dict,
        list,
        object,
        BaseException,
        Exception,
    }
    forbidden_roots = {
        "click",
        "fastapi",
        "google",
        "openai",
        "anthropic",
        "ollama",
        "prompt_toolkit",
        "pydantic",
        "rich",
    }
    for annotation in get_type_hints(contract_type).values():
        assert get_origin(annotation) not in {dict, list}
        for leaf in _annotation_leaves(annotation):
            assert leaf not in forbidden_types
            module_name = getattr(leaf, "__module__", "")
            assert module_name.split(".", maxsplit=1)[0] not in forbidden_roots


def test_public_application_contract_imports_without_ui_or_provider_frameworks() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src"
    script = """
import json
import sys
sys.path.insert(0, sys.argv[1])
import ancestryllm.application
import ancestryllm.application.dto
import ancestryllm.application.errors
import ancestryllm.application.operations
import ancestryllm.application.ports
forbidden = (
    "anthropic", "click", "fastapi", "google", "ollama", "openai",
    "prompt_toolkit", "pydantic", "rich",
)
loaded = sorted(
    name for name in sys.modules
    if name.split(".", 1)[0] in forbidden
)
print(json.dumps(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, str(source_root)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []


def test_ports_accept_transport_neutral_structural_implementations() -> None:
    class PortSet:
        def check_cancelled(self) -> None:
            return

        def emit(self, update: ProgressUpdate) -> None:
            del update

        def decide(self, request: dto_module.DecisionRequest) -> dto_module.DecisionResponse:
            return dto_module.DecisionResponse(
                request.decision_id,
                request.default_option_id,
                request.default_option_id is None,
            )

        def resolve_identity(
            self,
            request: dto_module.IdentityResolutionRequest,
        ) -> dto_module.IdentityResolutionResult:
            return dto_module.IdentityResolutionResult(
                request.resolution_id,
                selected_ref=request.candidates[0].candidate_ref,
            )

        def resolve_quality(
            self,
            request: dto_module.QualityResolutionRequest,
        ) -> dto_module.QualityResolutionResult:
            return dto_module.QualityResolutionResult(
                request.resolution_id,
                request.options[0],
            )

    implementation = PortSet()
    assert isinstance(implementation, CancellationPort)
    assert isinstance(implementation, ProgressPort)
    assert isinstance(implementation, DecisionPort)
    assert isinstance(implementation, IdentityResolutionPort)
    assert isinstance(implementation, QualityResolutionPort)
    NeverCancelled().check_cancelled()
    DiscardProgress().emit(ProgressUpdate("gedcom.merge", "start", 0))


def test_boundary_values_reject_paths_controls_and_non_finite_numbers() -> None:
    assert ProviderSelection().network_allowed is False
    with pytest.raises(ValueError, match="paths or control"):
        FailureDetail("destination", "/private/fixture.ged")
    with pytest.raises(ValueError, match="unsupported characters"):
        ProgressUpdate("gedcom/merge", "start", 0)
    with pytest.raises(ValueError, match="unsupported characters"):
        ArtifactRef(
            f"art_{'a' * 31}/unsafe",
            "text/plain",
            "report",
            1,
            ArtifactStatus.READY,
        )
    with pytest.raises(ValueError, match="finite"):
        operations_module.QueryRow((float("nan"),)).to_json()


def test_boundary_codec_rejects_unknown_fields_wrong_types_and_nonstandard_json() -> None:
    valid = operations_module.ModuleEnableRequest("gedcom")
    envelope = json.loads(valid.to_json())
    envelope["value"]["extra"] = True
    with pytest.raises(ValueError, match="Unknown"):
        operations_module.ModuleEnableRequest.from_json(json.dumps(envelope))

    envelope = json.loads(valid.to_json())
    envelope["value"]["module_id"] = 4
    with pytest.raises(TypeError, match="string"):
        operations_module.ModuleEnableRequest.from_json(json.dumps(envelope))

    with pytest.raises(ValueError, match="Invalid JSON constant"):
        operations_module.QueryRow.from_json(
            '{"contract":"' + CONTRACT_VERSION + '","type":"QueryRow","value":{"values":[NaN]}}'
        )


def test_domain_error_mapping_is_exhaustive_stable_and_sanitized() -> None:
    assert set(DOMAIN_ERROR_MAPPINGS) == set(DomainFailureCode)
    assert len({mapping.code for mapping in DOMAIN_ERROR_MAPPINGS.values()}) == len(
        DomainFailureCode
    )

    failure = DomainFailure(
        DomainFailureCode.INTERNAL,
        (DomainFailureDetail("attempt", 1),),
    )
    mapped = map_domain_failure(failure)
    envelope = error_envelope(mapped, correlation_ref="request-1")

    assert mapped.code == "APPLICATION_FAILURE"
    assert mapped.details == {"attempt": 1}
    assert envelope.details == (FailureDetail("attempt", 1),)
    assert "private" not in envelope.to_json()

    unsafe = AncestryError(
        "APPLICATION_FAILURE",
        "Safe failure.",
        details={
            "attempt": 2,
            "destination": "/private/fixture.ged",
            "exception": RuntimeError("private fixture content"),
        },
    )
    sanitized = error_envelope(unsafe)
    assert sanitized.details == (FailureDetail("attempt", 2),)


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (
            AncestryError("GEDCOM_ROOT_PERSON_UNRESOLVED", "private identity detail"),
            DomainFailureCode.IDENTITY_AMBIGUOUS,
        ),
        (
            FileIngressError("FILE_INPUT_TOO_LARGE", "private size detail"),
            DomainFailureCode.ARTIFACT_TOO_LARGE,
        ),
        (
            FileIngressError("FILE_ENCODING_INVALID", "private encoding detail"),
            DomainFailureCode.ARTIFACT_INVALID,
        ),
        (
            AncestryError("CONSENT_INACTIVE", "private consent detail"),
            DomainFailureCode.PROVIDER_CONSENT_REQUIRED,
        ),
        (
            ProviderError("PROVIDER_OUTPUT_INVALID", "private provider detail"),
            DomainFailureCode.PROVIDER_UNAVAILABLE,
        ),
        (
            AncestryError("GEDCOM_OVERWRITE_INPUT", "private destination detail"),
            DomainFailureCode.INVALID_REQUEST,
        ),
        (
            AncestryError("PROMPT_NOT_FOUND", "private resource detail"),
            DomainFailureCode.NOT_FOUND,
        ),
        (
            AncestryError("SYNC_PUBLICATION_INCOMPLETE", "private output detail"),
            DomainFailureCode.PUBLICATION_FAILED,
        ),
        (OSError("private host path"), DomainFailureCode.PUBLICATION_FAILED),
        (RuntimeError("private internal detail"), DomainFailureCode.INTERNAL),
    ),
)
def test_current_exceptions_map_to_sanitized_domain_failures(
    error: Exception,
    expected: DomainFailureCode,
) -> None:
    failure = domain_failure_from_exception(error)

    assert failure.code is expected
    assert failure.details == ()
    assert "private" not in str(failure)


def test_existing_domain_failure_is_preserved_by_current_exception_mapping() -> None:
    failure = DomainFailure(
        DomainFailureCode.CONFLICT,
        (DomainFailureDetail("conflicts", 2),),
    )

    assert domain_failure_from_exception(failure) is failure


def test_current_cancellation_adapter_maps_legacy_signal_without_detail() -> None:
    class LegacyCancellation:
        def check_cancelled(self) -> None:
            raise CancellationError("private fixture cancellation detail")

    with pytest.raises(DomainFailure) as caught:
        _CurrentCancellationAdapter(LegacyCancellation()).check_cancelled()

    assert caught.value.code is DomainFailureCode.CANCELLED
    assert str(caught.value) == "CANCELLED"


def test_current_progress_adapter_accepts_protocol_keyword_and_preserves_counts() -> None:
    class LegacyReporter:
        def __init__(self) -> None:
            self.updates: list[tuple[str, int | None, int | None]] = []

        def check_cancelled(self) -> None:
            return

        def update(
            self,
            operation: str,
            *,
            completed: int | None = None,
            total: int | None = None,
        ) -> None:
            self.updates.append((operation, completed, total))

    reporter = LegacyReporter()

    _CurrentProgressAdapter(reporter).emit(
        event=ProgressUpdate("gedcom.merge", "write", 1, completed=2, total=3),
    )

    assert reporter.updates == [("gedcom.merge.write", 2, 3)]


def test_artifact_grants_are_opaque_operation_scoped_and_revocable(tmp_path: Path) -> None:
    source = tmp_path / "fictional.ged"
    source.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    registry = _ArtifactRegistry()
    grant = registry.grant_input(
        source,
        operation="gedcom.merge",
        media_type="text/vnd.gedcom",
        artifact_type="gedcom",
    )

    descriptor = registry.describe_input(grant, operation="gedcom.merge")
    serialized = grant.to_json() + descriptor.to_json()
    assert str(tmp_path) not in serialized
    assert "0 HEAD" not in serialized
    assert descriptor.size_bytes == len(source.read_bytes())

    forged = ArtifactGrantRef(
        grant.grant_id,
        "gedcom.quality",
        ArtifactAccess.READ,
    )
    with pytest.raises(DomainFailure) as wrong_operation:
        registry.resolve(
            forged,
            operation="gedcom.quality",
            access=ArtifactAccess.READ,
        )
    assert wrong_operation.value.code is DomainFailureCode.ARTIFACT_FORBIDDEN

    registry.revoke(grant)
    with pytest.raises(DomainFailure) as revoked:
        registry.describe_input(grant, operation="gedcom.merge")
    assert revoked.value.code is DomainFailureCode.ARTIFACT_FORBIDDEN


def test_artifact_read_grant_rejects_replaced_input(tmp_path: Path) -> None:
    source = tmp_path / "fictional.ged"
    source.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    registry = _ArtifactRegistry()
    grant = registry.grant_input(
        source,
        operation="gedcom.merge",
        media_type="text/vnd.gedcom",
        artifact_type="gedcom",
    )
    replacement = tmp_path / "replacement.ged"
    replacement.write_text("0 HEAD\n1 SOUR FICTIONAL\n0 TRLR\n", encoding="utf-8")
    replacement.replace(source)

    with pytest.raises(DomainFailure) as changed:
        registry.describe_input(grant, operation="gedcom.merge")

    assert changed.value.code is DomainFailureCode.ARTIFACT_INVALID


def test_artifact_publication_is_atomic_and_returns_no_host_path(tmp_path: Path) -> None:
    destination = tmp_path / "report.json"
    registry = _ArtifactRegistry()
    grant = registry.grant_output(
        destination,
        operation="gedcom.quality",
        media_type="application/json",
        artifact_type="quality-report",
    )

    descriptor = registry.publish_text(
        grant,
        '{"status":"fictional"}\n',
        operation="gedcom.quality",
        cancellation=NeverCancelled(),
    )

    assert destination.read_text(encoding="utf-8") == '{"status":"fictional"}\n'
    assert str(tmp_path) not in descriptor.to_json()
    assert descriptor.status is ArtifactStatus.READY
    assert sorted(path.name for path in tmp_path.iterdir()) == ["report.json"]


def test_cancellation_before_publication_preserves_existing_artifact(tmp_path: Path) -> None:
    class CancelBeforePublication:
        def check_cancelled(self) -> None:
            raise DomainFailure(DomainFailureCode.CANCELLED)

    destination = tmp_path / "existing.ged"
    destination.write_bytes(b"existing fictional artifact")
    registry = _ArtifactRegistry()
    grant = registry.grant_output(
        destination,
        operation="gedcom.merge",
        media_type="text/vnd.gedcom",
        artifact_type="gedcom",
    )

    with pytest.raises(DomainFailure) as cancelled:
        registry.publish_bytes(
            grant,
            b"replacement fictional artifact",
            operation="gedcom.merge",
            cancellation=CancelBeforePublication(),
        )

    assert cancelled.value.code is DomainFailureCode.CANCELLED
    assert destination.read_bytes() == b"existing fictional artifact"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["existing.ged"]


def test_secret_request_serializes_only_write_only_capability() -> None:
    request = operations_module.SecretSetRequest(
        "openai",
        SecretGrantRef(f"sec_{'e' * 64}", "openai"),
    )

    serialized = request.to_json()

    assert "sk-fictional-secret" not in serialized
    assert "sec_" in serialized
