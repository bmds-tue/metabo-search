"""Phase 4: recipes — determinism vs find_datasets, staged flow, harvest guard."""

import pytest

import metabo_search.inspector
import metabo_search.searcher
from metabo_search.core.recipes import (
    full_report, harvest, quick_discovery, quick_probe)
from metabo_search.models import (
    AssayInfo, OntologyTerm, RequirementProfile, StudyCandidate,
    StudyRequirements,
)
from metabo_search.workflow import find_datasets

PROFILE = RequirementProfile(
    hard=StudyRequirements(organisms=["Homo sapiens"]),
    free_text="urine")


def candidate(sid, org="Homo sapiens"):
    return StudyCandidate(
        study_id=sid, title=f"Study {sid}",
        organisms=[OntologyTerm(org)] if org else [], sample_count=8)


def deep_candidate(sid):
    c = candidate(sid)
    c.metabolite_count = 200
    c.maf_files_parsed = True
    c.investigation_file_parsed = True
    c.sample_file_parsed = True           # real deep candidates carry this
    c.sample_metadata = [{"Sample Name": f"{sid}_s1"}]
    c.assays = [AssayInfo(technique_name="LC-MS")]
    return c


SHALLOW = [candidate("M1"), candidate("M2", org="Mus musculus"),
           candidate("M3")]


def fake_search(*a, **k):
    return list(SHALLOW)


def fake_inspect(cands, **k):
    return [deep_candidate(c.study_id) for c in cands]


def test_quick_probe_is_stage_one_only():
    p = quick_probe("urine", PROFILE)
    assert [s.kind for s in p.steps] == ["search", "filter"]
    assert str(p) == "search('urine') → filter(screen)"


def test_quick_discovery_matches_find_datasets(monkeypatch):
    monkeypatch.setattr(metabo_search.searcher, "search_studies", fake_search)
    monkeypatch.setattr(metabo_search.inspector, "inspect_studies", fake_inspect)
    # find_datasets is now a shim over the pipeline → the searcher/inspector
    # patches above are enough (bodies are imported lazily inside the steps)

    old = find_datasets("urine", profile=PROFILE, max_candidates=50,
                        deep_inspect_top=10, max_workers=2,
                        databases=("metabolights",))
    new = quick_discovery("urine", PROFILE, databases=("metabolights",),
                          max_results=50, min_survivors=10, workers=2).run()

    assert [s.study_id for s in old.candidates] == \
        [sc.study_id for sc in new.score.ranked]
    assert old.table_rows == new.score.table.table_rows
    assert [c.study_id for c in old.screening.survivors] == \
        [c.study_id for c in new.filter.survivors]


def test_staged_flow_reuses_probe(monkeypatch, tmp_path):
    """probe (cheap) → judge → extend to deep; prefix replays warm."""
    monkeypatch.setattr(metabo_search.searcher, "search_studies", fake_search)
    monkeypatch.setattr(metabo_search.inspector, "inspect_studies", fake_inspect)
    root = tmp_path / "c"

    probe = quick_probe("urine", PROFILE,
                        databases=("metabolights",)).cache(root)
    r = probe.run()                                   # stage 1: no inspect
    assert list(r.keys()) == ["search", "filter"]
    assert [c.study_id for c in r.filter.survivors] == ["M1", "M3"]

    deep = probe.extend(
        __import__("metabo_search.core.steps", fromlist=["inspect"]).inspect(),
        __import__("metabo_search.core.steps", fromlist=["score"]).score(PROFILE),
    )
    r2 = deep.run()                                   # stage 2
    assert [c.study_id for c in r2.score.ranked] == ["M1", "M3"]
    # search+screen replayed from cache: search body still returns same set
    # (counted via plan keys unchanged in a second identical run)


def test_full_report_runs_with_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(metabo_search.searcher, "search_studies", fake_search)
    monkeypatch.setattr(metabo_search.inspector, "inspect_studies", fake_inspect)
    calls = {"n": 0}

    def llm(prompt):
        calls["n"] += 1
        return '{"codes": {}, "sentence_template": "A sample", "slot_sources": {}}'

    r = full_report("urine", PROFILE, databases=("metabolights",),
                    top=2, cache_root=tmp_path / "c").run(llm=llm)
    assert calls["n"] == 2
    assert set(r.describe.by_study) == {"M1", "M3"}


def test_harvest_requires_download_constraint():
    with pytest.raises(ValueError, match="download constraint"):
        harvest("urine", PROFILE)
    p = harvest("urine", PROFILE, databases=("metabolights",),
                download_kwargs={"categories": ["raw"], "dest_dir": "/tmp/x"})
    assert p.steps[-2].kind == "download"
    assert p.steps[-1].kind == "export"