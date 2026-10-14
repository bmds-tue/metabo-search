"""The library's documented promises, asserted in one place.

Both suites call these functions:

* the **offline** suite runs them against fixtures and ``MockTransport``;
* the **live** suite runs the same functions against the real APIs.

A promise therefore has a single definition, and the live job checks that
upstream data still honours it. The functions raise ``AssertionError`` and
import no test framework, so they can be used from any test style.
"""

from __future__ import annotations

from collections.abc import Callable

from metabo_search.metabolights.models import Sample as MetaboLightsSample
from metabo_search.metabolights.models import StudyDetail as MetaboLightsDetail
from metabo_search.workbench.models import SampleRow
from metabo_search.workbench.models import StudyDetail as WorkbenchDetail
from metabo_search.workspace import IngestReport, Workspace

ML_AUDIT = "ml_ingest_run"
WB_AUDIT = "wb_ingest_run"


def _scalar(ws: Workspace, query: str, params: list[object] | None = None) -> object:
    row = ws.sql(query, params).fetchone()
    assert row is not None, f"query returned no row: {query}"
    return row[0]


def _count(ws: Workspace, table: str, study_id: str) -> int:
    return int(_scalar(ws, f'SELECT count(*) FROM "{table}" WHERE study_id = ?', [study_id]))


def _total(ws: Workspace, table: str) -> int:
    return int(_scalar(ws, f'SELECT count(*) FROM "{table}"'))


def data_counts(ws: Workspace, audit_table: str) -> dict[str, int]:
    """Row counts for every table except the audit table."""
    return {name: _total(ws, name) for name in ws.tables() if name != audit_table}


def audit_run_ids(ws: Workspace, audit_table: str) -> set[str]:
    """The distinct ``run_id`` values in an audit table (empty if not created)."""
    if audit_table not in ws.tables():
        return set()
    rows = ws.sql(f'SELECT DISTINCT run_id FROM "{audit_table}"').fetchall()
    return {str(row[0]) for row in rows}


def assert_metabolights_detail_round_trips(
    detail: MetaboLightsDetail, report: IngestReport, ws: Workspace
) -> None:
    """``ml_sample`` holds the distinct natural keys; children follow last-wins.

    The search index's ``sample_count`` is *not* the contract: variant ISA
    sheets legitimately collapse to fewer rows (e.g. MTBLS744: 4 -> 2), and the
    collapse must be reported in ``report.duplicates`` rather than thrown.
    """
    if detail.error:
        assert detail.study_id in report.skipped, "errored detail must be skipped"
        return
    sid = detail.study_id
    samples: list[MetaboLightsSample] = list(detail.samples)
    # Each table holds exactly its own distinct natural keys. Child tables key
    # on the child field too, so they do not collapse with the parent sample:
    # two rows sharing (name, row) but carrying different factors both survive.
    assert _count(ws, "ml_sample", sid) == len(
        {(s.sample_name, s.sample_row) for s in samples}
    ), "ml_sample natural keys"
    assert _count(ws, "ml_sample_characteristic", sid) == len(
        {(s.sample_name, s.sample_row, key) for s in samples for key in s.characteristics}
    ), "ml_sample_characteristic"
    assert _count(ws, "ml_sample_factor", sid) == len(
        {(s.sample_name, s.sample_row, name) for s in samples for name in s.factor_values}
    ), "ml_sample_factor"
    assert _count(ws, "ml_sample_file", sid) == len(
        {(s.sample_name, s.sample_row, "raw", path) for s in samples for path in s.raw_files}
        | {
            (s.sample_name, s.sample_row, "derived", path)
            for s in samples
            for path in s.derived_files
        }
    ), "ml_sample_file"
    assert _count(ws, "ml_metabolite", sid) == len(detail.metabolites), "ml_metabolite"
    assert _count(ws, "ml_metabolite_abundance", sid) == sum(
        len(m.abundances) for m in detail.metabolites
    ), "ml_metabolite_abundance"
    if detail.files:
        assert _count(ws, "ml_file", sid) == len(
            {f.path for f in detail.files}
        ), "ml_file natural keys"


def assert_workbench_detail_round_trips(
    detail: WorkbenchDetail, report: IngestReport, ws: Workspace
) -> None:
    """Workbench rows round-trip exactly on their natural keys."""
    if detail.error:
        assert detail.study_id in report.skipped, "errored detail must be skipped"
        return
    sid = detail.study_id
    samples: list[SampleRow] = list(detail.samples)
    assert _count(ws, "wb_sample", sid) == len(
        {(s.local_sample_id, s.mb_sample_id) for s in samples}
    ), "wb_sample natural keys"
    # wb_sample_factor keys on local_sample_id only, so count its own keys.
    assert _count(ws, "wb_sample_factor", sid) == len(
        {(s.local_sample_id, name) for s in samples for name in s.factor_values}
    ), "wb_sample_factor"
    assert _count(ws, "wb_analysis", sid) == len(
        {a.analysis_id for a in detail.analyses}
    ), "wb_analysis natural keys"
    assert _count(ws, "wb_metabolite", sid) == len(
        {(m.analysis_id, m.metabolite_name, m.refmet_name) for m in detail.metabolites}
    ), "wb_metabolite natural keys"


def assert_duplicates_reported(report: IngestReport, table: str, expected: int) -> None:
    """Collapsed natural keys are reported, never silently dropped or thrown.

    ``report.duplicates`` is per table across the whole batch, so call this
    with a report from a batch whose duplicate count you can compute.
    """
    actual = report.duplicates.get(table, 0)
    assert actual == expected, f"{table}: reported {actual} duplicates, expected {expected}"


def assert_skipped(report: IngestReport, expected: list[str]) -> None:
    """Exactly the expected ids are reported as skipped."""
    assert set(report.skipped) == set(expected), f"skipped {report.skipped} != {expected}"


def assert_idempotent(
    write: Callable[[], IngestReport], ws: Workspace, audit_table: str
) -> IngestReport:
    """Re-applying a batch changes no data table and appends one audit run."""
    before = data_counts(ws, audit_table)
    before_runs = audit_run_ids(ws, audit_table)
    report = write()
    after = data_counts(ws, audit_table)
    assert after == before, f"data tables changed on re-ingest: {before} -> {after}"
    assert len(audit_run_ids(ws, audit_table) - before_runs) == 1, "one new audit run"
    return report


def assert_no_table_shrinks(
    enrich: Callable[[], IngestReport], ws: Workspace, audit_table: str
) -> None:
    """Enriching a workspace never removes rows (upsert-only)."""
    before = data_counts(ws, audit_table)
    enrich()
    after = data_counts(ws, audit_table)
    for table, count in before.items():
        assert after.get(table, 0) >= count, (
            f"{table} shrank on enrichment: {count} -> {after.get(table, 0)}"
        )


def assert_audit_trail(
    report: IngestReport,
    ws: Workspace,
    audit_table: str,
    source: str,
    prior_run_ids: set[str],
) -> None:
    """One audit row per written table, with the right source and run id."""
    new = audit_run_ids(ws, audit_table) - prior_run_ids
    assert len(new) == 1, f"expected exactly one new audit run, got {len(new)}"
    run_id = next(iter(new))
    rows = ws.sql(
        f'SELECT table_name, source, rows FROM "{audit_table}" WHERE run_id = ?',
        [run_id],
    ).fetchall()
    by_table = {str(t): (str(s), int(r)) for t, s, r in rows}
    assert set(by_table) == set(report.tables), "audit must cover the written tables"
    assert all(s == source for s, _ in by_table.values()), "audit source"
    for table, (_, audit_rows) in by_table.items():
        assert audit_rows >= report.tables[table] >= 1, f"audit rows for {table}"
    assert audit_table not in report.tables, "audit table must be excluded from the report"


def assert_cross_source_isolated(ws: Workspace) -> None:
    """``ml_*`` and ``wb_*`` tables coexist without overlap."""
    tables = set(ws.tables())
    ml_tables = {t for t in tables if t.startswith("ml_")}
    wb_tables = {t for t in tables if t.startswith("wb_")}
    assert ml_tables, "no ml_* tables"
    assert wb_tables, "no wb_* tables"
    assert not (ml_tables & wb_tables), "ml_* and wb_* tables overlap"
    assert ml_tables | wb_tables <= tables
