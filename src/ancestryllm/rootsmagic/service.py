"""RootsMagic application service composed from stable feature boundaries."""

from __future__ import annotations

from pathlib import Path

from ancestryllm.core.config import AppConfig
from ancestryllm.core.ingress import FileIngressPolicy
from ancestryllm.llm.policy import ConsentGrant
from ancestryllm.llm.service import LLMService
from ancestryllm.rootsmagic.core import QueryResult, RootsMagicReader
from ancestryllm.rootsmagic.export import RootsMagicExporter, RootsMagicExportResult
from ancestryllm.rootsmagic.query import RootsMagicQueryService

__all__ = ["RootsMagicService"]


class RootsMagicService:
    def __init__(self, config: AppConfig, llm: LLMService | None = None) -> None:
        self.config = config
        ingress = FileIngressPolicy(config.file_ingress)
        self.reader = RootsMagicReader(
            config.family_tree_dirs,
            config.max_query_rows,
            config.query_timeout_seconds,
            ingress,
        )
        self.query_service = RootsMagicQueryService(config, self.reader, llm)
        self.exporter = RootsMagicExporter(self.reader)

    def list_trees(self) -> list[Path]:
        return self.reader.list_trees()

    def query_sql(self, tree: str | Path, sql: str) -> QueryResult:
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
        return self.query_service.query_question(
            tree,
            question,
            provider_id=provider_id,
            model=model,
            consent=consent,
        )

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
