"""The ``databases`` option — the only API change beyond the repositories.

Contract under test
-------------------
- ``None``/default ⇒ every registered repository, in DISPATCH order.
- ``databases=("metabolights",)`` behaves exactly like pre-change behavior:
  only MetaboLights candidates, tagged ``repository="metabolights"``.
- Bad names → ``ValueError`` at build time (``search()``).
- Merge order respects the tuple; every candidate is tagged.
- Per-db caching: changing ``databases`` reuses the unchanged db's outcome.
"""

import json

import pytest

from metabo_search import pipeline, search
from metabo_search.core.results import SearchResult
from metabo_search.repositories.base import DEFAULT_DATABASES, DISPATCH


def candidate(sid: str, repository: str = "metabolights"):
    from metabo_search.models import StudyCandidate
    c = StudyCandidate(study_id=sid, title=sid, repository=repository)
    return c


def monkey_ml(monkeypatch):
    """Stub the MetaboLights search body with deterministic candidates."""
    def fake_search(*a, **k):
        return [candidate("M1"), candidate("M2")]
    monkeypatch.setattr("metabo_search.searcher.search_studies", fake_search)


def test_default_databases_is_all():
    assert DEFAULT_DATABASES == ("metabolights", "metabolomics_workbench")
    assert "metabolomics_workbench" in DISPATCH


def test_search_factory_validates(monkeypatch):
    monkey_ml(monkeypatch)
    s = search("x", databases=("metabolights",))
    assert s.config.databases == ("metabolights",)
    with pytest.raises(ValueError, match="unknown database"):
        search("x", databases=("nope",))
    with pytest.raises(ValueError, match="at least one"):
        search("x", databases=())


def test_metabolights_only_unchanged(monkeypatch, tmp_path):
    """databases=('metabolights',) == pre-change behavior (no workbench)."""
    monkey_ml(monkeypatch)
    r = pipeline(search("urine", databases=("metabolights",))).cache(
        tmp_path / "c").run()
    res = r["search"]
    assert [c.study_id for c in res.candidates] == ["M1", "M2"]
    assert all(c.repository == "metabolights" for c in res.candidates)
    assert res.args_used["databases"] == ["metabolights"]


def test_default_merges_repositories(monkeypatch, tmp_path):
    """Default (all) → MetaboLights candidates first, then workbench."""
    monkey_ml(monkeypatch)
    r = pipeline(search("serum")).cache(tmp_path / "c").run()
    res = r["search"]
    repos = [(c.study_id, c.repository) for c in res.candidates]
    ml_ids = [sid for sid, repo in repos if repo == "metabolights"]
    wb_ids = [sid for sid, repo in repos if repo == "metabolomics_workbench"]
    assert ml_ids == ["M1", "M2"]                 # ML first (default order)
    assert wb_ids                                 # workbench contributed
    assert all(sid.startswith("ST") for sid in wb_ids)
    assert res.args_used["databases"] == ["metabolights",
                                          "metabolomics_workbench"]


def test_repository_tag_survives_result_trip(tmp_path):
    wb = candidate("ST000001", repository="metabolomics_workbench")
    res = SearchResult(candidates=[wb], query="x")
    rt = SearchResult.from_json(res.to_json())
    assert rt.candidates[0].repository == "metabolomics_workbench"


def test_per_db_cache_reuses_unchanged_db(monkeypatch, tmp_path):
    """Running all-dbs then metabolights-only must NOT hit ML again."""
    calls = {"ml": 0}

    def counting(*a, **k):
        calls["ml"] += 1
        return [candidate("M1")]

    monkeypatch.setattr("metabo_search.searcher.search_studies", counting)
    root = tmp_path / "c"
    pipeline(search("serum")).cache(root).run()          # both dbs
    assert calls["ml"] == 1

    r = pipeline(search("serum", databases=("metabolights",))).cache(
        root).run()                                       # ML-only re-run
    assert calls["ml"] == 1                               # reused per-db cache
    assert [c.study_id for c in r["search"].candidates] == ["M1"]

    r2 = pipeline(search("serum")).cache(root).run()      # step-level warm
    assert calls["ml"] == 1
    assert r["search"].digest() != r2["search"].digest()  # different databases


def test_workbench_only_no_ml(monkeypatch, tmp_path):
    monkey_ml(monkeypatch)
    r = pipeline(search("serum", databases=("metabolomics_workbench",))).cache(
        tmp_path / "c").run()
    cands = r["search"].candidates
    assert cands and all(c.repository == "metabolomics_workbench" for
                         c in cands)
    assert r["search"].args_used["databases"] == ["metabolomics_workbench"]


def test_plan_records_databases(monkeypatch, tmp_path):
    monkey_ml(monkeypatch)
    root = tmp_path / "c"
    pipeline(search("serum", databases=("metabolomics_workbench",))).cache(
        root).run()
    plan = json.loads((root / "plan.json").read_text())
    assert plan[0]["config"]["databases"] == ["metabolomics_workbench"]