"""Shared, source-agnostic ingestion mechanics.

A source declares its tables (column name -> SQL type, plus a natural key) and
a pure mapping from its models to rows. Everything else — DDL rendering, row
wrapping, the audit run, and error collection — is identical across sources and
lives here. This module imports no source package.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol, TypeVar, cast, runtime_checkable
from uuid import uuid4

from metabo_search.workspace import IngestBatch, IngestReport, TableRows, Workspace

TablesSpec = dict[str, tuple[dict[str, str], tuple[str, ...]]]
"""Table name -> (column name -> SQL type, natural key)."""


@runtime_checkable
class ErroredDetail(Protocol):
    """A per-study detail whose best-effort inspection failed."""

    study_id: str
    error: str


T = TypeVar("T", bound=ErroredDetail)


def render_ddl(tables: TablesSpec) -> str:
    """Render ``CREATE TABLE IF NOT EXISTS`` statements from a table spec."""
    statements: list[str] = []
    for name, (columns, keys) in tables.items():
        column_defs = ", ".join(
            f'"{column}" {sql_type}' for column, sql_type in columns.items()
        )
        key_defs = ", ".join(f'"{key}"' for key in keys)
        statements.append(
            f'CREATE TABLE IF NOT EXISTS "{name}" ({column_defs}, '
            f"PRIMARY KEY ({key_defs}))"
        )
    return ";\n".join(statements) + ";"


def to_table_rows(
    tables: TablesSpec, rows_by_name: dict[str, list[dict[str, object]]]
) -> list[TableRows]:
    """Wrap each table's pending rows with its declared columns and key."""
    return [
        TableRows(name=name, columns=tuple(columns), rows=rows_by_name[name], keys=keys)
        for name, (columns, keys) in tables.items()
    ]


def skipped_ids(obj: object, detail_type: type[T]) -> list[str]:
    """Study ids of errored ``detail_type`` items (best-effort inspect)."""
    items: list[object] = cast("list[object]", obj) if isinstance(obj, list) else [obj]
    return [
        item.study_id for item in items if isinstance(item, detail_type) and item.error
    ]


def write(
    ws: Workspace,
    batch: IngestBatch,
    *,
    source: str,
    tables: TablesSpec,
    audit_table: str,
    skipped: list[str],
) -> IngestReport:
    """Write a batch and its audit run in one transaction.

    One audit row per non-empty table (keyed by a fresh run id) is appended to
    the batch before :meth:`Workspace.write`, so the data and the trail commit
    together. The audit table is excluded from the report.

    Args:
        ws: The workspace to write to.
        batch: The data batch from a source's ``batch()``.
        source: Value recorded in the audit table's ``source`` column.
        tables: The source's full table spec, including the audit table.
        audit_table: Name of the audit table in ``tables``.
        skipped: Identifiers skipped because their data was unavailable.

    Returns:
        The report for the data tables, with ``skipped`` attached.
    """
    report = ws.write(
        _with_audit_run(batch, source=source, tables=tables, audit_table=audit_table)
    )
    counts = {name: count for name, count in report.tables.items() if name != audit_table}
    return replace(report, tables=counts, total=sum(counts.values()), skipped=skipped)


def _with_audit_run(
    batch: IngestBatch,
    *,
    source: str,
    tables: TablesSpec,
    audit_table: str,
) -> IngestBatch:
    """Return ``batch`` with one audit row per non-empty table appended.

    Counts are the batch's row counts (before dedupe), known without touching
    the database.
    """
    columns, keys = tables[audit_table]
    run_id = uuid4().hex
    at = datetime.now(UTC).isoformat()
    data_tables: list[TableRows] = []
    audit_rows: list[dict[str, object]] = []
    for table in batch.tables:
        if table.name == audit_table:
            continue
        data_tables.append(table)
        if table.rows:
            audit_rows.append(
                {
                    "run_id": run_id,
                    "table_name": table.name,
                    "source": source,
                    "rows": len(table.rows),
                    "at": at,
                }
            )
    if audit_rows:
        data_tables.append(
            TableRows(name=audit_table, columns=tuple(columns), rows=audit_rows, keys=keys)
        )
    return replace(batch, tables=data_tables)
