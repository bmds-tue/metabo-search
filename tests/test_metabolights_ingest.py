"""MetaboLights ingestion tests: pure mapping plus DuckDB round trips."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from contract import (
    ML_AUDIT,
    assert_audit_trail,
    assert_duplicates_reported,
    assert_idempotent,
    assert_metabolights_detail_round_trips,
    audit_run_ids,
)

from metabo_search.metabolights import ingest
from metabo_search.metabolights.models import (
    Funding,
    Metabolite,
    Protocol,
    ProtocolComponent,
    Sample,
    StudyDetail,
    StudyFile,
    StudyHit,
)
from metabo_search.results import Results
from metabo_search.workspace import IngestBatch, TableRows, Workspace

FIXTURES = Path(__file__).parent / "fixtures" / "metabolights"


def _hits() -> Results[StudyHit]:
    data = json.loads((FIXTURES / "search_page.json").read_text())
    return Results(StudyHit.model_validate(row) for row in data["content"]["results"])


def _rows(batch: IngestBatch, table: str) -> list[dict[str, object]]:
    for entry in batch.tables:
        if entry.name == table:
            return entry.rows
    raise KeyError(table)


def _detail() -> StudyDetail:
    return StudyDetail(
        study_id="MTBLS1",
        samples=[
            Sample(
                sample_name="s1",
                source_name="src",
                organism="Homo sapiens",
                organism_part="plasma",
                sample_type="blood plasma",
                characteristics={"Organism part": "plasma"},
                factor_values={"Gender": "F"},
                raw_files=["FILES/a.raw"],
                derived_files=["FILES/a.mzML"],
            )
        ],
        metabolites=[
            Metabolite(
                study_id="MTBLS1",
                file="m_x.tsv",
                row=0,
                database_identifier="CHEBI:1",
                metabolite_identification="glucose",
                chemical_formula="C6H12O6",
                mass_to_charge=180.06,
                retention_time=1.5,
                abundances={"s1": 12.5, "s2": 8.0},
            )
        ],
        files=[
            StudyFile(
                study_id="MTBLS1",
                path="FILES/a.raw",
                name="a.raw",
                directory="FILES",
                extension=".raw",
                size_bytes=10,
                category="raw",
            )
        ],
    )


def test_batch_study_hit_maps_scalars_and_children() -> None:
    hit = _hits()[0]
    batch = ingest.batch(hit)

    study = _rows(batch, "ml_study")
    assert len(study) == 1
    assert study[0]["study_id"] == "MTBLS1"
    assert study[0]["title"] == hit.title
    assert study[0]["sample_count"] == hit.sample_count

    facets = {(row["facet"], row["term"]) for row in _rows(batch, "ml_study_term")}
    assert ("organism", "Homo sapiens") in facets
    assert _rows(batch, "ml_study_lineage")
    assert _rows(batch, "ml_assay")
    assert _rows(batch, "ml_contact")


def test_batch_merges_a_sequence_into_one_batch() -> None:
    hits = _hits()
    batch = ingest.batch(hits)
    assert len(_rows(batch, "ml_study")) == len(hits)
    assert batch.ddl == ingest.DDL


def test_batch_study_detail_maps_samples_and_metabolites() -> None:
    batch = ingest.batch(_detail())
    sample = _rows(batch, "ml_sample")[0]
    assert sample["sample_name"] == "s1"
    assert sample["organism_part"] == "plasma"
    assert _rows(batch, "ml_sample_characteristic")[0]["key"] == "Organism part"
    assert _rows(batch, "ml_sample_factor")[0]["name"] == "Gender"
    kinds = {row["kind"] for row in _rows(batch, "ml_sample_file")}
    assert kinds == {"raw", "derived"}
    metabolite = _rows(batch, "ml_metabolite")[0]
    assert metabolite["metabolite_identification"] == "glucose"
    assert metabolite["mass_to_charge"] == 180.06
    abundances = _rows(batch, "ml_metabolite_abundance")
    assert {row["sample"]: row["value"] for row in abundances} == {"s1": 12.5, "s2": 8.0}


def test_batch_maps_fundings_and_protocol_components() -> None:
    hit = StudyHit(
        study_id="MTBLS1",
        fundings=[
            Funding(
                fund_ref_id="10.13039/501100000780",
                funder="European Union",
                grant_identifier="123",
            )
        ],
        protocols=[
            Protocol(
                name="Extraction",
                components=[
                    ProtocolComponent(
                        name="Instrument",
                        type="MS",
                        term_source_ref="OBI",
                        term_accession_number="OBI:0000470",
                    )
                ],
            )
        ],
    )
    batch = ingest.batch(hit)
    assert _rows(batch, "ml_funding") == [
        {
            "study_id": "MTBLS1",
            "ordinal": 0,
            "fund_ref_id": "10.13039/501100000780",
            "funder": "European Union",
            "grant_identifier": "123",
        }
    ]
    assert _rows(batch, "ml_protocol_component") == [
        {
            "study_id": "MTBLS1",
            "protocol_ordinal": 0,
            "ordinal": 0,
            "name": "Instrument",
            "type": "MS",
            "term_source_ref": "OBI",
            "term_accession_number": "OBI:0000470",
        }
    ]


def test_batch_study_file_uses_its_own_study_id() -> None:
    file = StudyFile(study_id="MTBLS9", path="FILES/x.txt", name="x.txt")
    rows = _rows(ingest.batch(file), "ml_file")
    assert rows == [
        {
            "study_id": "MTBLS9",
            "path": "FILES/x.txt",
            "name": "x.txt",
            "directory": "",
            "extension": "",
            "size_bytes": 0,
            "category": "",
        }
    ]


def test_batch_rejects_unknown_type() -> None:
    with pytest.raises(TypeError):
        ingest.batch(object())


def test_write_round_trips_and_is_idempotent() -> None:
    hits = _hits()
    with Workspace(":memory:") as ws:
        first = ingest.write(ws, hits)
        second = ingest.write(ws, hits)
        assert first.tables == second.tables
        assert first.total == second.total > 0
        assert ws.sql("SELECT count(*) FROM ml_study").fetchone() == (len(hits),)
        assert ws.sql("SELECT title FROM ml_study WHERE study_id = 'MTBLS1'").fetchone() == (
            hits[0].title,
        )


def test_write_detail_enriches_without_duplicating() -> None:
    with Workspace(":memory:") as ws:
        ingest.write(ws, _hits())
        ingest.write(ws, _detail())
        ingest.write(ws, _detail())
        assert ws.sql("SELECT count(*) FROM ml_sample").fetchone() == (1,)
        assert ws.sql("SELECT count(*) FROM ml_metabolite").fetchone() == (1,)
        assert ws.sql("SELECT count(*) FROM ml_file").fetchone() == (1,)


def test_write_skips_errored_detail() -> None:
    good = _detail()
    bad = StudyDetail(study_id="MTBLS999", error="network failure")
    with Workspace(":memory:") as ws:
        report = ingest.write(ws, Results([good, bad]))
        assert report.skipped == ["MTBLS999"]
        assert ws.sql("SELECT count(*) FROM ml_sample").fetchone() == (1,)


def test_write_records_the_audit_in_the_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If the audit merge fails, the data must roll back with it: one transaction.
    original = Workspace._merge

    def failing_merge(self: Workspace, table: TableRows) -> tuple[int, int]:
        if table.name == "ml_ingest_run":
            raise RuntimeError("audit failed")
        return original(self, table)

    monkeypatch.setattr(Workspace, "_merge", failing_merge)
    with Workspace(":memory:") as ws:
        with pytest.raises(RuntimeError):
            ingest.write(ws, _detail())
        # The DDL is transactional too, so nothing (data or audit) survives.
        assert ws.tables() == []


def test_batch_keeps_samples_that_share_a_name() -> None:
    # Regression: (study_id, sample_name) collapsed distinct samples; the row
    # index disambiguates them (MTBLS66 repeats "Control 1" 206 times).
    detail = StudyDetail(
        study_id="MTBLS66",
        samples=[
            Sample(sample_name="Control 1", sample_row=0, factor_values={"Replicate": "1"}),
            Sample(sample_name="Control 1", sample_row=1, factor_values={"Replicate": "2"}),
        ],
    )
    batch = ingest.batch(detail)
    assert [row["sample_row"] for row in _rows(batch, "ml_sample")] == [0, 1]
    factors = _rows(batch, "ml_sample_factor")
    assert {(row["sample_row"], row["value"]) for row in factors} == {(0, "1"), (1, "2")}


def test_write_does_not_collapse_samples_that_share_a_name() -> None:
    detail = StudyDetail(
        study_id="MTBLS66",
        samples=[
            Sample(sample_name="Control 1", sample_row=0),
            Sample(sample_name="Control 1", sample_row=1),
        ],
    )
    with Workspace(":memory:") as ws:
        report = ingest.write(ws, detail)
        assert report.duplicates == {}
        assert ws.sql("SELECT count(*) FROM ml_sample").fetchone() == (2,)


def test_write_records_an_audit_run() -> None:
    with Workspace(":memory:") as ws:
        ingest.write(ws, _detail())
        rows = ws.sql(
            "SELECT source, table_name, rows FROM ml_ingest_run ORDER BY table_name"
        ).fetchall()
        assert rows
        assert {row[0] for row in rows} == {"metabolights"}
        assert ("ml_sample", 1) in {(row[1], row[2]) for row in rows}


def test_contract_round_trips_offline() -> None:
    detail = _detail()
    with Workspace(":memory:") as ws:
        report = ingest.write(ws, detail)
        assert_metabolights_detail_round_trips(detail, report, ws)
        assert_duplicates_reported(report, "ml_sample", 0)


def test_contract_idempotent_offline() -> None:
    detail = _detail()
    with Workspace(":memory:") as ws:
        ingest.write(ws, detail)
        assert_idempotent(lambda: ingest.write(ws, detail), ws, ML_AUDIT)


def test_contract_audit_trail_offline() -> None:
    detail = _detail()
    with Workspace(":memory:") as ws:
        before = audit_run_ids(ws, ML_AUDIT)
        report = ingest.write(ws, detail)
        assert_audit_trail(report, ws, ML_AUDIT, "metabolights", before)
