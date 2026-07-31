"""Transport-neutral commands, results, accounting, and errors for GEDCOM sync."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from ancestryllm.core.cancellation import (
    cancellation_checkpoint,
)
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.ingress import (
    FileFingerprint,
)
from ancestryllm.gedcom.contracts import IdentityResolver

MANIFEST_SCHEMA_VERSION = 1

SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")

SUPPORTED_VENDORS = ("ancestry", "geni", "myheritage", "other")

CONTROLLED_TAGS = frozenset(
    {
        "ADOP",
        "MEDI",
        "PEDI",
        "QUAY",
        "ROLE",
        "SEX",
        "STAT",
        "TYPE",
    }
)

ATTACHMENT_TAGS = frozenset({"NOTE", "OBJE", "SOUR"})

SOURCE_ADMIN_TAGS = frozenset({"CHAN", "RIN"})

RECORD_PREFIXES = {
    "FAM": "F",
    "INDI": "I",
    "NOTE": "N",
    "OBJE": "O",
    "REPO": "R",
    "SOUR": "S",
}

EXIT_CODES = {
    "SYNC_CONFIGURATION": 2,
    "SYNC_PARSE": 3,
    "MANIFEST_INVALID": 4,
    "MANIFEST_MASTER_MISMATCH": 4,
    "SYNC_AMBIGUOUS": 5,
    "SYNC_UNSAFE_REMOVAL": 6,
    "SYNC_OUTPUT": 7,
    "SYNC_PUBLICATION_INCOMPLETE": 7,
}

ResolverFactory = Callable[[str, str, str | None], IdentityResolver]

CancellationCheck = Callable[[], None]


@dataclass(frozen=True, slots=True)
class SyncAccounting:
    """Deterministic domain accounting retained outside rendered transcripts."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: int = 0
    warnings: int = 0
    information: int = 0
    quality_warnings: int = 0
    errors: int = 0
    resolved: int = 0


@dataclass(frozen=True, slots=True)
class SyncExecutionResult:
    """Structured incremental result rendered only by an outer adapter."""

    exit_code: int
    output: str = ""
    error: str = ""
    committed: bool = False
    artifacts: tuple[Path, ...] = ()
    accounting: SyncAccounting = field(default_factory=SyncAccounting)


@dataclass(frozen=True, slots=True)
class SyncSnapshotInput:
    """Transport-neutral snapshot input used by the sync application service."""

    source_id: str
    vendor: str
    path: Path
    exported_at: str | None = None


@dataclass(frozen=True, slots=True)
class SyncUpdateCommand:
    """Typed update command shared by terminal parsing and application services."""

    master: Path
    release_root: Path
    provider: str
    snapshots: tuple[SyncSnapshotInput, ...]
    manifest: Path | None = None
    initialize_manifest: bool = False
    quality_root_person: str | None = None
    no_quality_report: bool = False
    dry_run: bool = False
    gedcom_version: str = "5.5.5"
    auto: bool = True


@dataclass(frozen=True, slots=True)
class SyncRebaseCommand:
    """Typed rebase command shared by terminal parsing and application services."""

    master: Path
    manifest: Path
    release_root: Path
    reason: str
    accept_manual_deletions: bool = False
    dry_run: bool = False


SyncCommand = SyncUpdateCommand | SyncRebaseCommand


@dataclass(frozen=True, slots=True)
class SnapshotSpec:
    """One stable website source ID and the newly exported GEDCOM snapshot."""

    source_id: str
    vendor: str
    path: Path
    exported_at: str
    date_basis: str
    sha256: str
    fingerprint: FileFingerprint

    @property
    def snapshot_id(self) -> str:
        """Return a stable content-addressed observation identifier."""
        return f"{self.source_id}:{self.sha256[:20]}"


@dataclass(slots=True)
class SyncStats:
    """Human-readable counters and details for one update operation."""

    added_people: list[str] = field(default_factory=list)
    mapped_people: list[str] = field(default_factory=list)
    unchanged_people: list[str] = field(default_factory=list)
    unresolved_people: list[str] = field(default_factory=list)
    added_facts: list[str] = field(default_factory=list)
    consolidated_facts: list[str] = field(default_factory=list)
    citations_attached: list[str] = field(default_factory=list)
    citations_deduplicated: list[str] = field(default_factory=list)
    source_records_consolidated: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    disappeared_retained: list[str] = field(default_factory=list)
    record_aliases: dict[str, str] = field(default_factory=dict)


def _checkpoint(cancellation_check: CancellationCheck | None = None) -> None:
    """Observe both the process token and an injected service cancellation port."""

    cancellation_checkpoint()
    if cancellation_check is not None:
        cancellation_check()


class SyncError(RuntimeError):
    """A safe operational failure with plain-English remediation."""

    def __init__(
        self,
        code: str,
        what: str,
        why: str,
        fixes: Sequence[str],
        *,
        details: Sequence[str] = (),
    ) -> None:
        super().__init__(what)
        self.code = code
        self.what = what
        self.why = why
        self.fixes = tuple(fixes)
        self.details = tuple(details)

    @property
    def exit_code(self) -> int:
        """Return the documented shell status for this error category."""
        return EXIT_CODES.get(self.code, 1)

    def render(self) -> str:
        """Return a troubleshooting message without raw genealogy content."""
        lines = [
            f"ERROR [{self.code}]",
            "",
            f"What happened: {self.what}",
            "",
            f"Why it matters: {self.why}",
            "",
            "How to fix it:",
        ]
        lines.extend(f"  {index}. {fix}" for index, fix in enumerate(self.fixes, 1))
        if self.details:
            lines.extend(["", "Details:"])
            lines.extend(f"  - {detail}" for detail in self.details)
        if self.code == "SYNC_PUBLICATION_INCOMPLETE":
            lines.extend(
                [
                    "",
                    "Publication state is incomplete; an app-owned directory may remain.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "No release was committed. An empty app-owned cleanup directory may remain.",
                ]
            )
        return "\n".join(lines) + "\n"

    def as_ancestry_error(self) -> AncestryError:
        """Return the transport-neutral coded form used by CLI and REPL services."""

        return AncestryError(
            self.code,
            self.what,
            " ".join(self.fixes),
            self.exit_code,
            {"error_class": self.code},
        )
