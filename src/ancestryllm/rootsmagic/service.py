"""RootsMagic application service composed from stable feature boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.application._rootsmagic_export import (
    RootsMagicExporter,
    RootsMagicExportResult,
)
from ancestryllm.core.ingress import FileIngressPolicy
from ancestryllm.rootsmagic.core import QueryResult, RootsMagicReader
from ancestryllm.rootsmagic.query import RootsMagicQueryService

if TYPE_CHECKING:
    from pathlib import Path

    from ancestryllm.application.operations import RootsMagicQueryRequest, RootsMagicQueryResult
    from ancestryllm.application.ports import CancellationPort, ProgressPort
    from ancestryllm.core.config import AppConfig
    from ancestryllm.llm.policy import ConsentGrant
    from ancestryllm.llm.service import LLMService

__all__ = ["RootsMagicService"]


class RootsMagicService:
    """Coordinate RootsMagic operations across the application boundary."""

    def __init__(
        self,
        config: AppConfig,
        llm: LLMService | None = None,
        *,
        progress: ProgressPort | None = None,
        cancellation: CancellationPort | None = None,
    ) -> None:
        self.config = config
        ingress = FileIngressPolicy(config.file_ingress)
        self.reader = RootsMagicReader(
            config.family_tree_dirs,
            config.max_query_rows,
            config.query_timeout_seconds,
            ingress,
        )
        self.query_service = RootsMagicQueryService(
            config,
            self.reader,
            llm,
            progress=progress,
            cancellation=cancellation,
        )
        self.exporter = RootsMagicExporter(self.reader)

    def list_trees(self) -> list[Path]:
        """Return immutable RootsMagic trees found beneath allowed directories."""
        return self.reader.list_trees()

    def query_sql(self, tree: str | Path, sql: str) -> QueryResult:
        """Execute validated read-only SQL against an immutable RootsMagic source."""
        return self.query_service.query_sql(tree, sql)

    def query_question(
        self,
        tree: str | Path,
        question: str,
        *,
        provider_id: str,
        model: str,
        consent: ConsentGrant | None = None,
    ) -> QueryResult:
        """Translate a question into validated read-only RootsMagic access."""
        return self.query_service.query_question(
            tree,
            question,
            provider_id=provider_id,
            model=model,
            consent=consent,
        )

    def execute_query(
        self,
        request: RootsMagicQueryRequest,
        *,
        consent: ConsentGrant | None = None,
    ) -> RootsMagicQueryResult:
        """Execute a typed query request through the application boundary."""

        return self.query_service.execute(request, consent=consent)

    def export(
        self,
        tree: str | Path,
        output: Path,
        *,
        profile: str = "portable",
        gedcom_version: str = "5.5.5",
        destination: str = "generic",
        root_person_id: str | None = None,
        scope: str = "connected",
        generations: int | None = None,
        living: str = "exclude",
        report_path: Path | None = None,
    ) -> RootsMagicExportResult:
        """Export an immutable RootsMagic source as loss-minimal GEDCOM."""
        return self.exporter.export(
            self.reader.resolve_tree(tree),
            output,
            profile=profile,
            gedcom_version=gedcom_version,
            destination=destination,
            root_person_id=root_person_id,
            scope=scope,
            generations=generations,
            living=living,
            report_path=report_path,
        )
