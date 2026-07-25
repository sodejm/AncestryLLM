"""Hardened, immutable SQLite access for RootsMagic databases."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from sqlglot import exp, parse

from ancestryllm.core.errors import AncestryError, SecurityPolicyError
from ancestryllm.core.ingress import FileIngressPolicy, FileKind

DENIED_ACTIONS = {
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_INDEX,
    sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER,
    sqlite3.SQLITE_DROP_TEMP_VIEW,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_REINDEX,
    sqlite3.SQLITE_ANALYZE,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_PRAGMA,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
}
FORBIDDEN_EXPRESSIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Transaction,
    exp.Merge,
)


def sha256_file(path: Path) -> str:
    digest, _ = FileIngressPolicy().sha256(path, FileKind.ROOTSMAGIC)
    return digest


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    sql: str
    truncated: bool


class RootsMagicReader:
    """Open a configured RootsMagic file only through SQLite read-only mode."""

    def __init__(
        self,
        allowed_directories: list[Path],
        max_rows: int = 100,
        timeout_seconds: float = 10.0,
        ingress: FileIngressPolicy | None = None,
    ) -> None:
        self.allowed_directories = [path.expanduser().resolve() for path in allowed_directories]
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self.ingress = ingress or FileIngressPolicy()

    def list_trees(self) -> list[Path]:
        results: set[Path] = set()
        for directory in self.allowed_directories:
            if directory.is_dir():
                for path in directory.glob("*.rmtree"):
                    try:
                        self.ingress.inspect(path, FileKind.ROOTSMAGIC)
                    except AncestryError as exc:
                        if exc.code == "FILE_INPUT_UNREADABLE" and not path.exists():
                            continue
                        raise
                    results.add(path.absolute())
                    self.ingress.validate_record(
                        FileKind.ROOTSMAGIC,
                        count=1,
                        byte_count=0,
                        nesting=0,
                        collection_items=len(results),
                    )
        return sorted(results)

    def resolve_tree(self, name_or_path: str | Path) -> Path:
        requested = Path(name_or_path).expanduser()
        candidates: list[Path]
        if requested.is_absolute():
            candidates = [requested]
        else:
            name = requested if requested.suffix == ".rmtree" else requested.with_suffix(".rmtree")
            candidates = [directory / name for directory in self.allowed_directories]
        for candidate in candidates:
            absolute = candidate.absolute()
            resolved = candidate.resolve(strict=False)
            if not any(
                os.path.commonpath([str(directory), str(resolved)]) == str(directory)
                for directory in self.allowed_directories
            ):
                continue
            if absolute.suffix.casefold() != ".rmtree":
                continue
            try:
                self.ingress.inspect(absolute, FileKind.ROOTSMAGIC)
            except AncestryError as exc:
                if exc.code == "FILE_INPUT_UNREADABLE" and not os.path.lexists(absolute):
                    continue
                raise
            return absolute
        raise AncestryError(
            "ROOTSMAGIC_TREE_NOT_FOUND",
            f"No configured RootsMagic database matches {str(name_or_path)!r}.",
            "Add its parent directory to config.toml and try again.",
        )

    @staticmethod
    def _authorizer(
        action: int, first: str | None, second: str | None, _db: str | None, _source: str | None
    ) -> int:
        if action in DENIED_ACTIONS:
            return sqlite3.SQLITE_DENY
        if (
            action == sqlite3.SQLITE_FUNCTION
            and (second or first or "").casefold() == "load_extension"
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @contextmanager
    def connection(self, path: Path) -> Iterator[sqlite3.Connection]:
        snapshot = self.ingress.inspect(path, FileKind.ROOTSMAGIC)
        uri = f"file:{path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=min(self.timeout_seconds, 30.0))
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.enable_load_extension(False)
            connection.set_authorizer(self._authorizer)
            deadline = time.monotonic() + self.timeout_seconds
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1_000)
            yield connection
        finally:
            connection.set_progress_handler(None, 0)
            connection.close()
            self.ingress.assert_unchanged(path, FileKind.ROOTSMAGIC, snapshot)

    def schema(self, path: Path) -> dict[str, tuple[str, ...]]:
        try:
            with self.connection(path) as connection:
                cursor = connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
                maximum = self.ingress.limit(FileKind.ROOTSMAGIC).max_collection_items
                rows = cursor.fetchmany(maximum + 1) if maximum is not None else cursor.fetchall()
                if maximum is not None and len(rows) > maximum:
                    self.ingress.validate_record(
                        FileKind.ROOTSMAGIC,
                        count=1,
                        byte_count=0,
                        nesting=0,
                        collection_items=len(rows),
                    )
                result: dict[str, tuple[str, ...]] = {}
                # PRAGMA is denied after the authorizer is installed, so parse
                # declared CREATE TABLE SQL.
                for table_name, create_sql in rows:
                    try:
                        parsed = parse(str(create_sql), read="sqlite")[0]
                        if parsed is None:
                            raise ValueError("empty CREATE TABLE expression")
                        columns = tuple(
                            column.this.name
                            for column in parsed.find_all(exp.ColumnDef)
                            if getattr(column.this, "name", None)
                        )
                    except Exception:  # noqa: BLE001 - vendor schemas can be unusual
                        columns = ()
                    result[str(table_name)] = columns
                return result
        except AncestryError:
            raise
        except sqlite3.Error as exc:
            raise AncestryError(
                "ROOTSMAGIC_INPUT_INVALID",
                "The RootsMagic input could not be inspected as a SQLite database.",
                details={"error_type": type(exc).__name__},
            ) from exc

    def validate_sql(self, sql: str, allowed_schema: dict[str, tuple[str, ...]]) -> str:
        if not sql.strip() or "\x00" in sql:
            raise SecurityPolicyError("SQL_REJECTED", "The generated SQL is empty or malformed.")
        try:
            statements = parse(sql, read="sqlite")
        except Exception as exc:
            raise SecurityPolicyError(
                "SQL_REJECTED", "The generated SQL could not be parsed."
            ) from exc
        if len(statements) != 1:
            raise SecurityPolicyError("SQL_REJECTED", "Exactly one SQL statement is allowed.")
        statement = statements[0]
        if not isinstance(statement, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise SecurityPolicyError("SQL_REJECTED", "Only SELECT or CTE queries are allowed.")
        for forbidden in FORBIDDEN_EXPRESSIONS:
            if statement.find(forbidden):
                raise SecurityPolicyError("SQL_REJECTED", "A forbidden SQL operation was detected.")
        allowed_tables = {name.casefold() for name in allowed_schema}
        referenced = {table.name.casefold() for table in statement.find_all(exp.Table)}
        if not referenced.issubset(allowed_tables):
            raise SecurityPolicyError(
                "SQL_TABLE_DENIED",
                "The query references a table outside the inspected RootsMagic schema.",
                details={"denied": sorted(referenced - allowed_tables)},
            )
        statement = statement.limit(self.max_rows + 1)
        return statement.sql(dialect="sqlite")

    def validate_row_limits(self, path: Path, schema: dict[str, tuple[str, ...]]) -> None:
        """Bound source rows before queries, exports, or provider schema use."""

        maximum = self.ingress.limit(FileKind.ROOTSMAGIC).max_records
        if maximum is None:
            return
        try:
            with self.connection(path) as connection:
                for table_name in schema:
                    quoted = table_name.replace('"', '""')
                    row = connection.execute(
                        f'SELECT COUNT(*) FROM (SELECT 1 FROM "{quoted}" LIMIT ?)',  # noqa: S608
                        (maximum + 1,),
                    ).fetchone()
                    count = int(row[0]) if row is not None else 0
                    self.ingress.validate_record(
                        FileKind.ROOTSMAGIC,
                        count=count,
                        byte_count=0,
                        nesting=0,
                        collection_items=1,
                    )
        except AncestryError:
            raise
        except sqlite3.Error as exc:
            raise AncestryError(
                "ROOTSMAGIC_ROW_LIMIT_UNVERIFIED",
                "The RootsMagic row limit could not be verified safely.",
                details={"error_type": type(exc).__name__},
            ) from exc

    def query(self, path: Path, sql: str) -> QueryResult:
        schema = self.schema(path)
        self.validate_row_limits(path, schema)
        validated = self.validate_sql(sql, schema)
        before = sha256_file(path)
        try:
            with self.connection(path) as connection:
                cursor = connection.execute(validated)
                columns = tuple(description[0] for description in cursor.description or ())
                raw_rows = cursor.fetchmany(self.max_rows + 1)
        except sqlite3.Error as exc:
            if "not authorized" in str(exc).casefold():
                raise SecurityPolicyError(
                    "SQL_OPERATION_DENIED",
                    "SQLite blocked an operation forbidden by the read-only policy.",
                ) from exc
            raise AncestryError(
                "ROOTSMAGIC_QUERY_FAILED",
                "The read-only RootsMagic query failed.",
                details={"error_type": type(exc).__name__},
            ) from exc
        after = sha256_file(path)
        if before != after:
            raise SecurityPolicyError(
                "ROOTSMAGIC_FILE_CHANGED",
                "The RootsMagic file changed while it was being queried.",
                "Close RootsMagic and use a stable backup copy before retrying.",
            )
        truncated = len(raw_rows) > self.max_rows
        return QueryResult(
            columns, tuple(tuple(row) for row in raw_rows[: self.max_rows]), validated, truncated
        )

    def read_table(self, path: Path, table_name: str) -> list[dict[str, Any]]:
        schema = self.schema(path)
        actual = next((name for name in schema if name.casefold() == table_name.casefold()), None)
        if actual is None:
            return []
        quoted = actual.replace('"', '""')
        try:
            with self.connection(path) as connection:
                connection.row_factory = sqlite3.Row
                # The identifier is selected from the inspected schema and quoted above.
                cursor = connection.execute(f'SELECT * FROM "{quoted}"')  # noqa: S608
                maximum = self.ingress.limit(FileKind.ROOTSMAGIC).max_records
                rows = cursor.fetchmany(maximum + 1) if maximum is not None else cursor.fetchall()
                if maximum is not None and len(rows) > maximum:
                    self.ingress.validate_record(
                        FileKind.ROOTSMAGIC,
                        count=len(rows),
                        byte_count=0,
                        nesting=0,
                        collection_items=1,
                    )
        except AncestryError:
            raise
        except sqlite3.Error as exc:
            raise AncestryError(
                "ROOTSMAGIC_READ_FAILED",
                "The RootsMagic table could not be read safely.",
                details={"error_type": type(exc).__name__},
            ) from exc
        return [dict(row) for row in rows]
