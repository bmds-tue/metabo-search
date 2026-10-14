"""Metabolomics Workbench models → DuckDB rows.

Mirrors :mod:`metabo_search.metabolights.ingest`: a pure :func:`batch` mapping
and a thin :func:`write` against a :class:`Workspace`. The two sources stay
independent — no shared tables, no shared mapping code.
"""

from __future__ import annotations

from typing import cast

import metabo_search.ingest as _shared
from metabo_search.workbench.models import (
    AnalysisRecord,
    MetaboliteRow,
    SampleRow,
    StudyDetail,
    StudySummary,
)
from metabo_search.workspace import IngestBatch, IngestReport, Workspace

SOURCE = "metabolomics_workbench"
"""Value recorded in the ``source`` column of the audit table."""

_AUDIT_TABLE = "wb_ingest_run"

# Table name -> (column name -> SQL type, natural key). DDL is generated from
# this, so the schema and the primary keys cannot drift apart.
_TABLES: _shared.TablesSpec = {
    "wb_study": (
        {
            "study_id": "VARCHAR",
            "study_title": "VARCHAR",
            "species": "VARCHAR",
            "institute": "VARCHAR",
            "analysis_type": "VARCHAR",
            "number_of_samples": "INTEGER",
            "submission_date": "VARCHAR",
            "release_date": "VARCHAR",
            "version": "VARCHAR",
            "revision_no": "VARCHAR",
            "revision_datetime": "VARCHAR",
            "revision_comment": "VARCHAR",
            "license": "VARCHAR",
            "license_url": "VARCHAR",
            "study_url": "VARCHAR",
        },
        ("study_id",),
    ),
    "wb_sample": (
        {
            "study_id": "VARCHAR",
            "local_sample_id": "VARCHAR",
            "mb_sample_id": "VARCHAR",
            "sample_source": "VARCHAR",
            "factors": "VARCHAR",
            "raw_data": "VARCHAR",
        },
        ("study_id", "local_sample_id", "mb_sample_id"),
    ),
    "wb_sample_factor": (
        {
            "study_id": "VARCHAR",
            "local_sample_id": "VARCHAR",
            "name": "VARCHAR",
            "value": "VARCHAR",
        },
        ("study_id", "local_sample_id", "name"),
    ),
    "wb_analysis": (
        {
            "study_id": "VARCHAR",
            "analysis_id": "VARCHAR",
            "analysis_summary": "VARCHAR",
            "analysis_type": "VARCHAR",
            "chromatography_system": "VARCHAR",
            "column_name": "VARCHAR",
            "chromatography_type": "VARCHAR",
            "ms_instrument_name": "VARCHAR",
            "ms_instrument_type": "VARCHAR",
            "ms_type": "VARCHAR",
            "ion_mode": "VARCHAR",
            "nmr_instrument_type": "VARCHAR",
            "nmr_experiment_type": "VARCHAR",
            "spectrometer_frequency": "VARCHAR",
            "nmr_solvent": "VARCHAR",
            "units": "VARCHAR",
        },
        ("study_id", "analysis_id"),
    ),
    "wb_metabolite": (
        {
            "study_id": "VARCHAR",
            "analysis_id": "VARCHAR",
            "analysis_summary": "VARCHAR",
            "metabolite_name": "VARCHAR",
            "refmet_name": "VARCHAR",
        },
        ("study_id", "analysis_id", "metabolite_name", "refmet_name"),
    ),
    _AUDIT_TABLE: (
        {
            "run_id": "VARCHAR",
            "table_name": "VARCHAR",
            "source": "VARCHAR",
            "rows": "BIGINT",
            "at": "VARCHAR",
        },
        ("run_id", "table_name"),
    ),
}


DDL = _shared.render_ddl(_TABLES)
"""DDL for every table this source writes (idempotent ``CREATE IF NOT EXISTS``)."""

# Fields that do not land in a same-named column: they expand into child
# tables or are deliberately not persisted. The completeness test asserts these
# cover every remaining model field, so a new upstream field fails the test.
_EXPANDED: dict[type[object], set[str]] = {
    StudySummary: set(),
    SampleRow: {"factor_values"},
    AnalysisRecord: set(),
    MetaboliteRow: set(),
    StudyDetail: {"summary", "samples", "analyses", "metabolites", "error"},
}
"""Per-model fields not stored under a same-named column (see the note above)."""


def batch(obj: object) -> IngestBatch:
    """Map Workbench models to an ingestion batch. Pure: no DB, no network.

    Args:
        obj: A single model, or a list/``Results`` of models. Accepted types:
            :class:`StudySummary` and :class:`StudyDetail`.

    Returns:
        One :class:`IngestBatch` with the source DDL and a table entry per
        ``wb_*`` table (empty tables are skipped by ``Workspace.write``).

    Raises:
        TypeError: An item is not an ingestible Workbench model.
    """
    items: list[object] = cast("list[object]", obj) if isinstance(obj, list) else [obj]
    rows_by_name: dict[str, list[dict[str, object]]] = {name: [] for name in _TABLES}
    for item in items:
        _map(item, rows_by_name)
    return IngestBatch(ddl=DDL, tables=_shared.to_table_rows(_TABLES, rows_by_name))


def write(ws: Workspace, obj: object) -> IngestReport:
    """Write models to ``ws`` idempotently and record an audit row.

    Args:
        ws: The workspace to write to.
        obj: Same as :func:`batch`.

    Returns:
        The per-table row counts (the audit table is excluded), plus the ids
        of any errored ``StudyDetail`` objects that were skipped (their data
        is empty by construction).
    """
    return _shared.write(
        ws,
        batch(obj),
        source=SOURCE,
        tables=_TABLES,
        audit_table=_AUDIT_TABLE,
        skipped=_shared.skipped_ids(obj, StudyDetail),
    )


def _add(
    tables: dict[str, list[dict[str, object]]],
    table: str,
    row: dict[str, object],
) -> None:
    """Append one row to a table's pending rows."""
    tables[table].append(row)


def _map(item: object, tables: dict[str, list[dict[str, object]]]) -> None:
    """Dispatch one model to its mapping function."""
    if isinstance(item, StudySummary):
        _study_summary(item, tables)
    elif isinstance(item, StudyDetail):
        # Best-effort inspect: an errored detail has no data to write.
        if not item.error:
            _study_detail(item, tables)
    else:
        raise TypeError(f"cannot ingest {type(item).__name__} into Workbench tables")


def _study_summary(
    summary: StudySummary, tables: dict[str, list[dict[str, object]]]
) -> None:
    """Map one whole-index study summary."""
    _add(
        tables,
        "wb_study",
        {
            "study_id": summary.study_id,
            "study_title": summary.study_title,
            "species": summary.species,
            "institute": summary.institute,
            "analysis_type": summary.analysis_type,
            "number_of_samples": summary.number_of_samples,
            "submission_date": summary.submission_date,
            "release_date": summary.release_date,
            "version": summary.version,
            "revision_no": summary.revision_no,
            "revision_datetime": summary.revision_datetime,
            "revision_comment": summary.revision_comment,
            "license": summary.license,
            "license_url": summary.license_url,
            "study_url": summary.study_url,
        },
    )


def _study_detail(
    detail: StudyDetail, tables: dict[str, list[dict[str, object]]]
) -> None:
    """Map deep per-study data: summary, samples, analyses, metabolites."""
    if detail.summary is not None:
        _study_summary(detail.summary, tables)
    for sample in detail.samples:
        _sample(sample, tables)
    for analysis in detail.analyses:
        _add(
            tables,
            "wb_analysis",
            {
                "study_id": analysis.study_id,
                "analysis_id": analysis.analysis_id,
                "analysis_summary": analysis.analysis_summary,
                "analysis_type": analysis.analysis_type,
                "chromatography_system": analysis.chromatography_system,
                "column_name": analysis.column_name,
                "chromatography_type": analysis.chromatography_type,
                "ms_instrument_name": analysis.ms_instrument_name,
                "ms_instrument_type": analysis.ms_instrument_type,
                "ms_type": analysis.ms_type,
                "ion_mode": analysis.ion_mode,
                "nmr_instrument_type": analysis.nmr_instrument_type,
                "nmr_experiment_type": analysis.nmr_experiment_type,
                "spectrometer_frequency": analysis.spectrometer_frequency,
                "nmr_solvent": analysis.nmr_solvent,
                "units": analysis.units,
            },
        )
    for metabolite in detail.metabolites:
        _add(
            tables,
            "wb_metabolite",
            {
                "study_id": metabolite.study_id,
                "analysis_id": metabolite.analysis_id,
                "analysis_summary": metabolite.analysis_summary,
                "metabolite_name": metabolite.metabolite_name,
                "refmet_name": metabolite.refmet_name,
            },
        )


def _sample(sample: SampleRow, tables: dict[str, list[dict[str, object]]]) -> None:
    """Map one sample row and explode its parsed factors into child rows."""
    _add(
        tables,
        "wb_sample",
        {
            "study_id": sample.study_id,
            "local_sample_id": sample.local_sample_id,
            "mb_sample_id": sample.mb_sample_id,
            "sample_source": sample.sample_source,
            "factors": sample.factors,
            "raw_data": sample.raw_data,
        },
    )
    for name, value in sample.factor_values.items():
        _add(
            tables,
            "wb_sample_factor",
            {
                "study_id": sample.study_id,
                "local_sample_id": sample.local_sample_id,
                "name": name,
                "value": value,
            },
        )
