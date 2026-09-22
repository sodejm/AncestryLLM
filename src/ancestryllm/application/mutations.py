"""Path-free contracts for durable, local mutation ownership."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from ancestryllm.application.dto import ArtifactRef, BoundaryDTO


def _opaque(value: str, length: int) -> None:
    if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        raise ValueError("Mutation identifiers must be opaque lowercase hexadecimal values.")


def _artifacts(value: tuple[ArtifactRef, ...]) -> None:
    if (
        not isinstance(value, tuple)
        or len(value) > 256
        or any(not isinstance(item, ArtifactRef) for item in value)
    ):
        raise ValueError("Mutation artifacts must be a bounded tuple of artifact references.")
    if any(
        re.fullmatch(r"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+", item.media_type) is None
        for item in value
    ):
        raise ValueError("Mutation artifact MIME types must omit parameters and paths.")


class MutationState(StrEnum):
    """Persisted states; unresolved ownership is never silently abandoned."""

    PREPARED = "prepared"
    COMMITTING = "committing"
    COMMITTED = "committed"
    ABORTED = "aborted"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True, slots=True)
class MutationResource(BoundaryDTO):
    """One opaque canonical scope and the revision authorized by its caller."""

    resource_id: str
    expected_revision: str

    def __post_init__(self) -> None:
        _opaque(self.resource_id, 64)
        _opaque(self.expected_revision, 64)


@dataclass(frozen=True, slots=True)
class MutationRequest(BoundaryDTO):
    """Normalized intent; paths and credentials must never enter this contract."""

    operation_id: str
    resources: tuple[MutationResource, ...]
    intent_digest: str
    idempotency_key: str
    owner_id: str
    session_id: str
    deadline_ms: int
    lease_ms: int
    artifacts: tuple[ArtifactRef, ...]
    retain_outcome: bool = True

    def __post_init__(self) -> None:
        for value in (self.operation_id, self.owner_id, self.session_id):
            _opaque(value, 32)
        for value in (self.intent_digest, self.idempotency_key):
            _opaque(value, 64)
        if (
            not isinstance(self.resources, tuple)
            or not 1 <= len(self.resources) <= 256
            or any(not isinstance(item, MutationResource) for item in self.resources)
        ):
            raise ValueError("A mutation requires between one and 256 resource scopes.")
        if len({item.resource_id for item in self.resources}) != len(self.resources):
            raise ValueError("Mutation resource scopes must be unique.")
        if type(self.deadline_ms) is not int or self.deadline_ms < 1:
            raise ValueError("A mutation requires a deadline.")
        if type(self.lease_ms) is not int or not 1 <= self.lease_ms <= 300_000:
            raise ValueError("Mutation leases must be bounded to at most five minutes.")
        if type(self.retain_outcome) is not bool:
            raise ValueError("Mutation outcome retention must be boolean.")
        _artifacts(self.artifacts)


@dataclass(frozen=True, slots=True)
class MutationLease(BoundaryDTO):
    """Fenced ownership; valid only while the issuing adapter retains its locks."""

    operation_id: str
    token: str
    fence: int
    expires_ms: int
    state: MutationState

    def __post_init__(self) -> None:
        _opaque(self.operation_id, 32)
        _opaque(self.token, 32)
        if type(self.fence) is not int or self.fence < 1:
            raise ValueError("A mutation fence must be positive.")
        if type(self.expires_ms) is not int or self.expires_ms < 1:
            raise ValueError("A mutation lease requires an expiration.")
        if not isinstance(self.state, MutationState) or self.state not in {
            MutationState.PREPARED,
            MutationState.COMMITTING,
            MutationState.RECOVERY_REQUIRED,
        }:
            raise ValueError("A mutation lease must be nonterminal.")


@dataclass(frozen=True, slots=True)
class MutationTransition(BoundaryDTO):
    """Requested lifecycle change and optional existing artifact references."""

    state: MutationState
    artifacts: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.state, MutationState):
            raise ValueError("A mutation transition requires a recognized state.")
        _artifacts(self.artifacts)


@dataclass(frozen=True, slots=True)
class MutationOutcome(BoundaryDTO):
    """Authoritative terminal result retained for matching retries."""

    operation_id: str
    state: MutationState
    artifacts: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        _opaque(self.operation_id, 32)
        if not isinstance(self.state, MutationState) or self.state not in {
            MutationState.COMMITTED,
            MutationState.ABORTED,
        }:
            raise ValueError("A mutation outcome must be terminal.")
        _artifacts(self.artifacts)


__all__ = [
    "MutationLease",
    "MutationOutcome",
    "MutationRequest",
    "MutationResource",
    "MutationState",
    "MutationTransition",
]
