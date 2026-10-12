"""A source-agnostic DuckDB working set.

A :class:`Workspace` is a thin handle over a DuckDB database: it knows about
tables, rows, and SQL, and nothing about MetaboLights or Workbench. Ingestion
lives in the source packages, which depend on this module — never the reverse.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import duckdb

DEFAULT_PATH = "metabo.duckdb"
"""Workspace location when none is given."""


@dataclass(frozen=True)
class TableRows:
    """Rows for one table, with the columns and natural key they use.

    Attributes:
        name: Table name.
        columns: Column names, in DDL order.
        rows: Row records; each maps every column name to a value.
        keys: Natural-key columns (also the table's ``PRIMARY KEY``).
    """

    name: str
    columns: tuple[str, ...]
    rows: list[dict[str, object]]
    keys: tuple[str, ...]


@dataclass(frozen=True)
class IngestBatch:
    """A unit of ingestion: the source DDL plus the rows to upsert.

    Attributes:
        ddl: ``CREATE TABLE IF NOT EXISTS`` statements for every table the
            source can write. Executed on every :meth:`Workspace.write`.
        tables: Rows to upsert, one entry per table.
    """

    ddl: str
    tables: list[TableRows]


@dataclass(frozen=True)
class IngestReport:
    """Outcome of an ingestion call.

    Attributes:
        tables: Table name to number of rows written (non-empty tables only).
        total: Total rows written.
        skipped: Identifiers skipped because their data was unavailable, e.g.
            a MetaboLights study whose best-effort inspection failed.
    """

    tables: dict[str, int]
    total: int
    skipped: list[str]


class Workspace:
    """A DuckDB working set: a connection, an idempotent write, and SQL.

    Args:
        path: Database file path, or ``":memory:"`` for an ephemeral database.
    """

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self._path = str(path)
        self._con = duckdb.connect(self._path)

    @property
    def path(self) -> str:
        """The database path this workspace is connected to."""
        return self._path

    def write(self, batch: IngestBatch) -> IngestReport:
        """Apply a batch idempotently.

        Executes the batch DDL, then upserts each table's rows on its natural
        key, so applying the same batch twice changes nothing. Empty tables are
        skipped: DuckDB rejects an empty ``executemany``.

        Args:
            batch: The DDL and rows to write.

        Returns:
            Per-table row counts (non-empty tables) and the total.

        Raises:
            AssertionError: The batch is malformed — no DDL, a table with no
                columns or keys, a key that is not a column, or a row whose
                keys do not match the declared columns.
        """
        assert batch.ddl.strip(), "batch.ddl must be non-empty"
        for table in batch.tables:
            assert table.columns, f"{table.name}: no columns"
            assert table.keys, f"{table.name}: no natural key"
            assert set(table.keys) <= set(table.columns), (
                f"{table.name}: keys {table.keys} are not all columns"
            )
            for row in table.rows:
                assert set(row) == set(table.columns), (
                    f"{table.name}: row keys {sorted(row)} != columns "
                    f"{sorted(table.columns)}"
                )

        counts: dict[str, int] = {}
        self._con.begin()
        try:
            self._con.execute(batch.ddl)
            for table in batch.tables:
                if not table.rows:
                    continue
                columns = ", ".join(f'"{column}"' for column in table.columns)
                placeholders = ", ".join("?" for _ in table.columns)
                self._con.executemany(
                    f'INSERT OR REPLACE INTO "{table.name}" ({columns}) '
                    f"VALUES ({placeholders})",
                    [
                        tuple(row[column] for column in table.columns)
                        for row in table.rows
                    ],
                )
                counts[table.name] = len(table.rows)
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return IngestReport(tables=counts, total=sum(counts.values()), skipped=[])

    def sql(
        self, query: str, params: Sequence[object] | None = None
    ) -> duckdb.DuckDBPyRelation:
        """Run a query and return DuckDB's own relation, unwrapped.

        Use ``.fetchall()``, ``.pl()``, ``.df()``, … as usual; no wrapper type
        is imposed.

        Args:
            query: SQL text.
            params: Optional positional parameters.
        """
        return self._con.sql(query, params=None if params is None else list(params))

    def tables(self) -> list[str]:
        """Return the workspace's table names, sorted."""
        rows = self._con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        return [str(row[0]) for row in rows]

    def schema(self, table: str | None = None) -> str:
        """Return the DDL for one table, or for every table.

        Args:
            table: Table name, or ``None`` for all tables.
        """
        if table is None:
            rows = self._con.execute(
                "SELECT sql FROM duckdb_tables() WHERE schema_name = 'main' "
                "ORDER BY table_name"
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT sql FROM duckdb_tables() WHERE table_name = ?", [table]
            ).fetchall()
        return "\n".join(str(row[0]) for row in rows)

    def close(self) -> None:
        """Close the underlying connection."""
        self._con.close()

    def __enter__(self) -> Workspace:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
