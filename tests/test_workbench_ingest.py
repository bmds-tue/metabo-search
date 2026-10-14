"""Workbench ingestion tests: pure mapping plus DuckDB round trips."""

from __future__ import annotations

import pytest
from contract import (
    WB_AUDIT,
    assert_idempotent,
    assert_workbench_detail_round_trips,
)

from metabo_search.results import Results
from metabo_search.workbench import ingest
from metabo_search.workbench.models import (
    AnalysisRecord,
    MetaboliteRow,
    SampleRow,
    StudyDetail,
    StudySummary,
)
from metabo_search.workspace import IngestBatch, TableRows, Workspace


def _rows(batch: IngestBatch, table: str) -> list[dict[str, object]]:
    for entry in batch.tables:
        if entry.name == table:
            return entry.rows
    raise KeyError(table)


def _summary(study_id: str = "ST000001") -> StudySummary:
    return StudySummary(
        study_id=study_id,
        study_title="Fatb Induction Experiment",
        species="Arabidopsis thaliana",
        analysis_type="GC-MS",
        number_of_samples=24,
    )


def _detail() -> StudyDetail:
    return StudyDetail(
        study_id="ST000001",
        summary=_summary(),
        samples=[
            SampleRow(
                study_id="ST000001",
                local_sample_id="LabF_115904",
                mb_sample_id="SA000019",
                sample_source="Plant",
                factors="Arabidopsis Genotype:fatb-ko KD",
                factor_values={"Arabidopsis Genotype": "fatb-ko KD"},
            )
        ],
        analyses=[
            AnalysisRecord(
                study_id="ST000001",
                analysis_id="AN000001",
                analysis_summary="GCMS positive ion mode",
                ms_instrument_name="Leco Pegasus III GC TOF",
            )
        ],
        metabolites=[
            MetaboliteRow(
                study_id="ST000001",
                analysis_id="AN000001",
                metabolite_name="glucose",
                refmet_name="Glucose",
            )
        ],
    )


def test_batch_study_summary_maps_scalars() -> None:
    rows = _rows(ingest.batch(_summary()), "wb_study")
    assert rows == [
        {
            "study_id": "ST000001",
            "study_title": "Fatb Induction Experiment",
            "species": "Arabidopsis thaliana",
            "institute": "",
            "analysis_type": "GC-MS",
            "number_of_samples": 24,
            "submission_date": "",
            "release_date": "",
            "version": "",
            "revision_no": "",
            "revision_datetime": "",
            "revision_comment": "",
            "license": "",
            "license_url": "",
            "study_url": "",
        }
    ]


def test_batch_study_detail_maps_all_tables() -> None:
    batch = ingest.batch(_detail())
    assert _rows(batch, "wb_study")[0]["study_id"] == "ST000001"
    assert _rows(batch, "wb_sample")[0]["local_sample_id"] == "LabF_115904"
    factor = _rows(batch, "wb_sample_factor")[0]
    assert factor == {
        "study_id": "ST000001",
        "local_sample_id": "LabF_115904",
        "name": "Arabidopsis Genotype",
        "value": "fatb-ko KD",
    }
    assert _rows(batch, "wb_analysis")[0]["ms_instrument_name"] == "Leco Pegasus III GC TOF"
    assert _rows(batch, "wb_metabolite")[0]["refmet_name"] == "Glucose"


def test_batch_detail_without_summary_skips_wb_study() -> None:
    detail = StudyDetail(study_id="ST000001")
    assert _rows(ingest.batch(detail), "wb_study") == []


def test_batch_merges_a_sequence() -> None:
    batch = ingest.batch(Results([_summary("ST000001"), _summary("ST000002")]))
    assert [row["study_id"] for row in _rows(batch, "wb_study")] == [
        "ST000001",
        "ST000002",
    ]


def test_batch_rejects_unknown_type() -> None:
    with pytest.raises(TypeError):
        ingest.batch(object())


def test_write_round_trips_and_is_idempotent() -> None:
    detail = _detail()
    with Workspace(":memory:") as ws:
        first = ingest.write(ws, detail)
        second = ingest.write(ws, detail)
        assert first.tables == second.tables
        assert first.total == second.total > 0
        assert ws.sql("SELECT count(*) FROM wb_sample").fetchone() == (1,)
        assert ws.sql("SELECT count(*) FROM wb_metabolite").fetchone() == (1,)


def test_write_skips_errored_detail() -> None:
    good = _detail()
    bad = StudyDetail(study_id="ST999999", error="no summary")
    with Workspace(":memory:") as ws:
        report = ingest.write(ws, Results([good, bad]))
        assert report.skipped == ["ST999999"]
        assert ws.sql("SELECT count(*) FROM wb_sample").fetchone() == (1,)


def test_write_records_the_audit_in_the_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If the audit merge fails, the data must roll back with it: one transaction.
    original = Workspace._merge

    def failing_merge(self: Workspace, table: TableRows) -> tuple[int, int]:
        if table.name == "wb_ingest_run":
            raise RuntimeError("audit failed")
        return original(self, table)

    monkeypatch.setattr(Workspace, "_merge", failing_merge)
    with Workspace(":memory:") as ws:
        with pytest.raises(RuntimeError):
            ingest.write(ws, _detail())
        # The DDL is transactional too, so nothing (data or audit) survives.
        assert ws.tables() == []


def test_write_records_an_audit_run() -> None:
    with Workspace(":memory:") as ws:
        ingest.write(ws, _detail())
        rows = ws.sql("SELECT source, table_name, rows FROM wb_ingest_run").fetchall()
        assert rows
        assert {row[0] for row in rows} == {"metabolomics_workbench"}


def test_contract_round_trips_offline() -> None:
    detail = _detail()
    with Workspace(":memory:") as ws:
        report = ingest.write(ws, detail)
        assert_workbench_detail_round_trips(detail, report, ws)


def test_contract_idempotent_offline() -> None:
    detail = _detail()
    with Workspace(":memory:") as ws:
        ingest.write(ws, detail)
        assert_idempotent(lambda: ingest.write(ws, detail), ws, WB_AUDIT)
