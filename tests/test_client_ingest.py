"""The clients' ``ingest()`` convenience delegates to the source mapping."""

from __future__ import annotations

from metabo_search.metabolights.client import MetaboLights
from metabo_search.metabolights.models import StudyHit
from metabo_search.workbench.client import Workbench
from metabo_search.workbench.models import StudySummary
from metabo_search.workspace import Workspace


def test_metabolights_ingest_delegates() -> None:
    hit = StudyHit(study_id="MTBLS1", title="A study")
    with MetaboLights() as ml, Workspace(":memory:") as ws:
        report = ml.ingest(hit, ws)
        assert report.total > 0
        assert ws.sql("SELECT title FROM ml_study").fetchone() == ("A study",)


def test_workbench_ingest_delegates() -> None:
    summary = StudySummary(study_id="ST000001", study_title="A study")
    with Workbench() as wb, Workspace(":memory:") as ws:
        report = wb.ingest(summary, ws)
        assert report.total > 0
        assert ws.sql("SELECT study_title FROM wb_study").fetchone() == ("A study",)
