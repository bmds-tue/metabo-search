"""A source-agnostic DuckDB working set.

A :class:`Workspace` is a thin handle over a DuckDB database: it knows about
tables, rows, and SQL, and nothing about MetaboLights or Workbench. Ingestion
lives in the source packages, which depend on this module — never the reverse.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import duckdb
import pyarrow as pa

DEFAULT_PATH = "metabo.duckdb"
"""Workspace location when none is given."""

_INCOMING = "_ingest_incoming"
"""Name of the registered Arrow view used for set-based upserts."""

_CHUNK = 250_000
"""Rows converted to Arrow and merged per round, bounding peak memory."""


def _chunks(rows: list[dict[str, object]], size: int) -> Iterator[list[dict[str, object]]]:
    """Yield ``rows`` in slices of at most ``size``."""
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _dedupe(table: TableRows) -> tuple[list[dict[str, object]], int]:
    """Collapse rows sharing a natural key (last occurrence wins).

    Returns the unique rows, in first-seen order, and how many were collapsed.
    Doing this ourselves keeps the winner deterministic — DuckDB's set-based
    ``ON CONFLICT`` does not define which of two same-key input rows survives.
    """
    index: dict[tuple[object, ...], int] = {}
    unique: list[dict[str, object]] = []
    collapsed = 0
    for row in table.rows:
        key = tuple(row[column] for column in table.keys)
        if key in index:
            unique[index[key]] = row
            collapsed += 1
        else:
            index[key] = len(unique)
            unique.append(row)
    return unique, collapsed


_ARROW_TYPES: dict[str, pa.DataType] = {
    "VARCHAR": pa.string(),
    "INTEGER": pa.int32(),
    "BIGINT": pa.int64(),
    "DOUBLE": pa.float64(),
}
"""SQL types used by the sources, mapped to Arrow types."""


def _arrow_schema(con: duckdb.DuckDBPyConnection, table: str) -> pa.Schema:
    """Build an Arrow schema matching a DuckDB table's columns and types."""
    rows = con.execute(f'DESCRIBE "{table}"').fetchall()
    fields: list[tuple[str, pa.DataType]] = []
    for row in rows:
        name, sql_type = str(row[0]), str(row[1]).upper()
        arrow_type = _ARROW_TYPES.get(sql_type)
        assert arrow_type is not None, f"no Arrow type for {sql_type!r} ({table}.{name})"
        fields.append((name, arrow_type))
    return pa.schema(fields)


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
        duplicates: Tables whose incoming rows collapsed on a repeated natural
            key (table name -> number of collapsed rows).
    """

    tables: dict[str, int]
    total: int
    skipped: list[str]
    duplicates: dict[str, int]


class Workspace:
    """A DuckDB working set: a connection, an idempotent write, and SQL.

    Args:
        path: Database file path, or ``":memory:"`` for an ephemeral database.
        memory_limit: DuckDB memory cap, e.g. ``"4GB"``. ``None`` leaves the
            DuckDB default.
        threads: Worker threads. ``None`` leaves the DuckDB default.
        preserve_insertion_order: Whether DuckDB preserves insertion order in
            query results. ``None`` leaves the DuckDB default.
    """

    def __init__(
        self,
        path: str | Path = DEFAULT_PATH,
        *,
        memory_limit: str | None = None,
        threads: int | None = None,
        preserve_insertion_order: bool | None = None,
    ) -> None:
        self._path = str(path)
        self._con = duckdb.connect(self._path)
        if memory_limit is not None:
            self._con.execute(f"SET memory_limit = '{memory_limit}'")
        if threads is not None:
            assert threads >= 1, "threads must be >= 1"
            self._con.execute(f"SET threads = {threads}")
        if preserve_insertion_order is not None:
            flag = "true" if preserve_insertion_order else "false"
            self._con.execute(f"SET preserve_insertion_order = {flag}")

    @property
    def path(self) -> str:
        """The database path this workspace is connected to."""
        return self._path

    def write(self, batch: IngestBatch) -> IngestReport:
        """Apply a batch idempotently.

        Executes the batch DDL, then upserts each table's rows on its natural
        key, so applying the same batch twice changes nothing. Empty tables are
        skipped.

        Args:
            batch: The DDL and rows to write.

        Returns:
            Per-table row counts (non-empty tables), the total, and any tables
            whose incoming rows collapsed on a repeated natural key.

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
        duplicates: dict[str, int] = {}
        self._con.begin()
        try:
            self._con.execute(batch.ddl)
            for table in batch.tables:
                if not table.rows:
                    continue
                rows, collapsed = _dedupe(table)
                if collapsed:
                    duplicates[table.name] = collapsed
                self._merge(table, rows)
                counts[table.name] = len(rows)
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise
        return IngestReport(
            tables=counts,
            total=sum(counts.values()),
            skipped=[],
            duplicates={name: n for name, n in duplicates.items() if n},
        )

    def _merge(self, table: TableRows, rows: list[dict[str, object]]) -> None:
        """Upsert one table's rows through Arrow, chunked.

        Rows are converted to Arrow using the target table's own schema and
        registered as a view, so DuckDB bulk-loads and merges them set-based.
        This avoids both the per-row conflict resolution (and memory blow-up)
        of ``INSERT OR REPLACE`` and the slow row-by-row ``executemany`` path.
        """
        schema = _arrow_schema(self._con, table.name)
        keys = ", ".join(f'"{key}"' for key in table.keys)
        non_keys = [column for column in table.columns if column not in table.keys]
        if non_keys:
            updates = ", ".join(
                f'"{column}" = excluded."{column}"' for column in non_keys
            )
            conflict = f"DO UPDATE SET {updates}"
        else:
            conflict = "DO NOTHING"
        for chunk in _chunks(rows, _CHUNK):
            incoming = pa.Table.from_pylist(chunk, schema=schema)
            self._con.register(_INCOMING, incoming)
            try:
                self._con.execute(
                    f'INSERT INTO "{table.name}" SELECT * FROM "{_INCOMING}" '
                    f"ON CONFLICT ({keys}) {conflict}"
                )
            finally:
                self._con.unregister(_INCOMING)

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
            "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main' "
            "ORDER BY table_name"
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
