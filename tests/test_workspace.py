"""Workspace tests: idempotent writes, SQL, schema (offline, in-memory)."""

from __future__ import annotations

import pytest

from metabo_search.workspace import IngestBatch, TableRows, Workspace

DDL = "CREATE TABLE IF NOT EXISTS t (id VARCHAR, value INTEGER, PRIMARY KEY (id))"


def _batch(rows: list[dict[str, object]]) -> IngestBatch:
    return IngestBatch(
        ddl=DDL,
        tables=[TableRows(name="t", columns=("id", "value"), rows=rows, keys=("id",))],
    )


def test_write_round_trips() -> None:
    with Workspace(":memory:") as ws:
        report = ws.write(_batch([{"id": "a", "value": 1}, {"id": "b", "value": 2}]))
        assert report.tables == {"t": 2}
        assert report.total == 2
        assert report.skipped == []
        assert ws.sql("SELECT id, value FROM t ORDER BY id").fetchall() == [
            ("a", 1),
            ("b", 2),
        ]


def test_write_is_idempotent() -> None:
    with Workspace(":memory:") as ws:
        batch = _batch([{"id": "a", "value": 1}])
        ws.write(batch)
        ws.write(batch)
        assert ws.sql("SELECT count(*) FROM t").fetchone() == (1,)
        assert ws.sql("SELECT value FROM t").fetchone() == (1,)


def test_write_upserts_on_natural_key() -> None:
    with Workspace(":memory:") as ws:
        ws.write(_batch([{"id": "a", "value": 1}]))
        ws.write(_batch([{"id": "a", "value": 9}]))
        assert ws.sql("SELECT value FROM t").fetchone() == (9,)


def test_write_skips_empty_tables() -> None:
    with Workspace(":memory:") as ws:
        report = ws.write(_batch([]))
        assert report.tables == {}
        assert report.total == 0
        assert ws.tables() == ["t"]  # DDL still ran


def test_write_rejects_row_with_wrong_columns() -> None:
    with Workspace(":memory:") as ws:
        batch = _batch([{"id": "a", "value": 1, "extra": 2}])
        with pytest.raises(AssertionError):
            ws.write(batch)


def test_write_rejects_key_not_a_column() -> None:
    with Workspace(":memory:") as ws:
        batch = IngestBatch(
            ddl=DDL,
            tables=[TableRows(name="t", columns=("id",), rows=[], keys=("nope",))],
        )
        with pytest.raises(AssertionError):
            ws.write(batch)


def test_write_rejects_missing_ddl() -> None:
    with Workspace(":memory:") as ws, pytest.raises(AssertionError):
        ws.write(IngestBatch(ddl="  ", tables=[]))


def test_tables_and_schema() -> None:
    with Workspace(":memory:") as ws:
        ws.write(_batch([]))
        assert ws.tables() == ["t"]
        ddl = ws.schema("t")
        assert "CREATE TABLE t" in ddl
        assert "PRIMARY KEY" in ddl
        assert ws.schema() == ddl


def test_sql_accepts_params() -> None:
    with Workspace(":memory:") as ws:
        ws.write(_batch([{"id": "a", "value": 1}]))
        assert ws.sql("SELECT value FROM t WHERE id = ?", ["a"]).fetchone() == (1,)

