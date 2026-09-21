"""Workbench deep inspect — factors/analysis/metabolites → StudyCandidate."""

from conftest import FIXTURES_DIR

from metabo_search.models import StudyCandidate
from metabo_search.repositories.workbench.inspect import (
    deep_metadata,
    sample_rows,
)


def _loader(study_id: str, kind: str) -> dict:
    import json
    return json.loads(
        (FIXTURES_DIR / f"study_{study_id}_{kind}.json").read_text())


def _shallow(sid: str = "ST000001") -> StudyCandidate:
    return StudyCandidate(study_id=sid, title=sid,
                          repository="metabolomics_workbench")


def test_deep_metadata_merges_factors_analysis_metabolites():
    c = _shallow()
    deep = deep_metadata([c], workers=2, load_payload=_loader)[0]
    assert deep.sample_count == 24                   # ST000001 factors rows
    assert deep.sample_metadata and "factors" in deep.sample_metadata[0]
    assert deep.assays and "MS" in deep.assays[0].technique_name
    assert deep.metabolite_count and deep.metabolite_count > 0
    assert deep.inspection_depth == "deep"


def test_deep_metadata_preserves_order():
    cands = [_shallow("ST000001"), _shallow("ST000002-no-fixture")]
    # ST000002 lacks fixtures → its fetch raises → the loader raises…
    # actually each study is fetched independently: soft-fail per study.
    deep = deep_metadata(cands, workers=2, load_payload=_loader)
    assert [c.study_id for c in deep] == \
        ["ST000001", "ST000002-no-fixture"]
    # ST000002 had no fixtures: leave shallow (soft failure)
    assert deep[1].inspection_depth == "shallow"


def test_sample_rows_flat_dicts():
    c = _shallow()
    deep = deep_metadata([c], workers=2, load_payload=_loader)[0]
    rows = sample_rows(deep)
    assert all(isinstance(r, dict) for r in rows)
    assert all("sample_name" in r for r in rows)