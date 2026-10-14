"""Live ingestion promises: round trips, idempotency, audit, isolation."""

from __future__ import annotations

import pytest
from anchors import ML_ANCHORS, ML_ERROR, WB_ANCHORS, WB_MISSING
from contract import (
    ML_AUDIT,
    WB_AUDIT,
    assert_audit_trail,
    assert_cross_source_isolated,
    assert_duplicates_reported,
    assert_idempotent,
    assert_metabolights_detail_round_trips,
    assert_no_table_shrinks,
    assert_skipped,
    assert_workbench_detail_round_trips,
    audit_run_ids,
)

from metabo_search import MetaboLights, Workbench, Workspace

pytestmark = pytest.mark.live


@pytest.fixture
def ws() -> Workspace:
    with Workspace(":memory:") as workspace:
        yield workspace


def test_metabolights_round_trips(ml: MetaboLights, ws: Workspace) -> None:
    details = ml.inspect(list(ML_ANCHORS), workers=1, include_metabolites=True)
    report = ml.ingest(details, ws)
    assert report.skipped == []
    for detail in details:
        assert_metabolights_detail_round_trips(detail, report, ws)


def test_metabolights_duplicate_samples_are_reported(
    ml: MetaboLights, ws: Workspace
) -> None:
    detail = ml.inspect(["MTBLS744"], workers=1, include_metabolites=False)[0]
    report = ml.ingest(detail, ws)
    keys = {(s.sample_name, s.sample_row) for s in detail.samples}
    assert_duplicates_reported(report, "ml_sample", len(detail.samples) - len(keys))
    assert_metabolights_detail_round_trips(detail, report, ws)


def test_metabolights_skips_errored_detail(ml: MetaboLights, ws: Workspace) -> None:
    details = ml.inspect([ML_ANCHORS[0], ML_ERROR], workers=1, include_metabolites=False)
    report = ml.ingest(details, ws)
    assert_skipped(report, [ML_ERROR])
    count = ws.sql(
        "SELECT count(*) FROM ml_sample WHERE study_id = ?", [ML_ERROR]
    ).fetchone()[0]
    assert count == 0


def test_metabolights_idempotent(ml: MetaboLights, ws: Workspace) -> None:
    detail = ml.inspect([ML_ANCHORS[0]], workers=1, include_metabolites=True)[0]
    ml.ingest(detail, ws)
    assert_idempotent(lambda: ml.ingest(detail, ws), ws, ML_AUDIT)


def test_metabolights_audit_trail(ml: MetaboLights, ws: Workspace) -> None:
    detail = ml.inspect([ML_ANCHORS[0]], workers=1, include_metabolites=False)[0]
    before = audit_run_ids(ws, ML_AUDIT)
    report = ml.ingest(detail, ws)
    assert_audit_trail(report, ws, ML_AUDIT, "metabolights", before)


def test_metabolights_upsert_only(ml: MetaboLights, ws: Workspace) -> None:
    hits = ml.search("", min_samples=5, limit=2)
    ml.ingest(hits, ws)
    details = ml.inspect(hits.ids(), workers=1, include_metabolites=False)
    assert_no_table_shrinks(lambda: ml.ingest(details, ws), ws, ML_AUDIT)


def test_workbench_round_trips(wb: Workbench, ws: Workspace) -> None:
    details = wb.inspect(list(WB_ANCHORS), workers=1)
    report = wb.ingest(details, ws)
    assert report.skipped == []
    for detail in details:
        assert_workbench_detail_round_trips(detail, report, ws)


def test_workbench_skips_errored_detail(wb: Workbench, ws: Workspace) -> None:
    details = wb.inspect([WB_ANCHORS[0], WB_MISSING], workers=1)
    report = wb.ingest(details, ws)
    assert_skipped(report, [WB_MISSING])


def test_workbench_idempotent(wb: Workbench, ws: Workspace) -> None:
    detail = wb.inspect([WB_ANCHORS[0]], workers=1)[0]
    wb.ingest(detail, ws)
    assert_idempotent(lambda: wb.ingest(detail, ws), ws, WB_AUDIT)


def test_cross_source_isolation(ml: MetaboLights, wb: Workbench, ws: Workspace) -> None:
    ml.ingest(ml.study(ML_ANCHORS[0]), ws)
    wb.ingest(wb.study(WB_ANCHORS[0]), ws)
    assert_cross_source_isolated(ws)
