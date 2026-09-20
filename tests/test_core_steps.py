"""Phases 2+3: step configs, validation, pipeline fold, cache replay."""

import json

import pytest

import mtbls_agent.inspector
from mtbls_agent.core.cache import CacheStore, parse_ttl
from mtbls_agent.core.results import (
    SearchResult, FilterResult, InspectResult, ScoreResult,
    DescribeResult, DownloadResult, ExportResult,
)
from mtbls_agent.core.steps import (
    CacheOpts, Pipeline, Screen, custom,
    describe, download, export, filter, inspect, maf, pipeline,
    register_predicate, score, search, screen,
)
from mtbls_agent.models import (
    AssayInfo, OntologyTerm, RequirementProfile, StudyCandidate,
    StudyRequirements,
)

PROFILE = RequirementProfile(
    hard=StudyRequirements(organisms=["Homo sapiens"]),
    free_text="urine")


def candidate(sid="MTBLS1", org="Homo sapiens"):
    return StudyCandidate(
        study_id=sid, title=f"Study {sid}",
        organisms=[OntologyTerm(org)] if org else [], sample_count=8)


def deep_candidate(sid="MTBLS1", samples=8, maf=200):
    c = candidate(sid)
    c.sample_count = samples
    c.metabolite_count = maf
    c.maf_files_parsed = maf > 0
    c.investigation_file_parsed = True
    c.assay_files_parsed = True
    c.sample_file_parsed = True
    c.assays = [AssayInfo(technique_name="LC-MS", ionization_mode="positive")]
    c.sample_metadata = [{"Sample Name": f"{sid}_s1", "Sample type": "patient"}]
    return c


def fake_inspect(cands, **kw):
    return [deep_candidate(c.study_id) for c in cands]


# ── configs / factories ─────────────────────────────────────────


def test_search_requires_query():
    with pytest.raises(ValueError):
        search("")
    with pytest.raises(TypeError):
        search()


def test_factories_and_ladder():
    p = pipeline(
        search("urine alzheimer"),
        filter(screen(profile=PROFILE, min_survivors=10)),
        inspect(),
        score(PROFILE),
    )
    assert str(p) == "search('urine alzheimer') → filter(screen) → inspect → score"
    p2 = p.extend(describe(top=2))
    assert len(p2.steps) == 5
    assert p2.steps[-1].kind == "describe"


def test_diff_shows_change():
    p1 = pipeline(search("urine"), filter(screen(profile=PROFILE)))
    p2 = pipeline(search("urine"), filter(screen(profile=PROFILE, min_survivors=5)))
    assert "screen" in p1.diff(p2)
    assert "unchanged" in p1.diff(p1)


# ── plan-time validation ────────────────────────────────────────


def test_validate_score_before_inspect():
    with pytest.raises(ValueError, match="score\\(\\) needs deep candidates"):
        pipeline(search("x"), score(PROFILE)).validate()


def test_validate_maf_on_shallow():
    with pytest.raises(ValueError, match="applies to InspectResult"):
        pipeline(search("x"), filter(maf(min_metabolites=10))).validate()


def test_validate_download_needs_constraint():
    p = pipeline(download(dest_dir="."), input=InspectResult([deep_candidate()]))
    with pytest.raises(ValueError, match="no constraint"):
        p.validate()


def test_validate_describe_needs_score():
    p = pipeline(describe(top=1), input=InspectResult([deep_candidate()]))
    with pytest.raises(ValueError, match="latest score"):
        p.validate()


def test_validate_download_needs_inspect():
    p = pipeline(download(categories=["raw"]),
                 input=SearchResult([candidate()]))
    with pytest.raises(ValueError, match="inspect"):
        p.validate()


def test_chained_filters_same_stage_allowed():
    """filter after filter on the same stage must validate (zoo bug)."""
    register_predicate(
        "min_samples2", InspectResult,
        lambda cands, params: ([c for c in cands
                                if (c.sample_count or 0) >= params["n"]],
                               [(c, "low samples") for c in cands
                                if (c.sample_count or 0) < params["n"]],
                               None))
    p = pipeline(
        filter(maf(min_metabolites=100)),
        filter(custom("min_samples2", n=5)),
        score(),
        input=InspectResult([deep_candidate("A", samples=8),
                             deep_candidate("B", samples=8)]),
    )
    p.validate()   # no raise
    r = p.run()
    assert r.order == ["filter", "filter#2", "score"]
    assert isinstance(r["filter#2"], FilterResult)
    assert [c.study_id for c in r["filter"].survivors] == ["A", "B"]


def test_validate_unknown_custom():
    with pytest.raises(ValueError, match="register_predicate"):
        pipeline(filter(custom("nope")), input=InspectResult([deep_candidate()])).validate()


def test_valid_flow_passes():
    p = pipeline(
        filter(maf(min_metabolites=100)),
        score(PROFILE),
        describe(top=1),
        download(categories=["raw"], dest_dir="."),
        export(path="out.csv"),
        input=InspectResult([deep_candidate()]),
    )
    p.validate()   # no raise


# ── fold (offline, bodies stubbed where needed) ─────────────────


def test_run_midstream_filter_offline():
    r = pipeline(
        filter(screen(profile=PROFILE)),
        input=SearchResult([candidate("M1"),
                            candidate("M2", org="Mus musculus")]),
    ).run()
    assert isinstance(r["filter"], FilterResult)
    assert [c.study_id for c in r.filter.survivors] == ["M1"]
    assert any("organism" in reason for _, reason in r.filter.dropped)
    assert r.filter.stage == "shallow"
    assert r["filter"].fmt(detail=True).count("✗") == 1


def test_run_offline_score_neutral_without_profile():
    r = pipeline(score(), input=InspectResult([deep_candidate("M1")])).run()
    assert isinstance(r.score, ScoreResult)
    assert len(r.score.ranked) == 1
    assert not r.score.ranked[0].score.hard_passed


def test_run_full_chain_offline(monkeypatch):
    monkeypatch.setattr(mtbls_agent.inspector, "inspect_studies", fake_inspect)
    calls = {"llm": 0}

    def llm(prompt):
        calls["llm"] += 1
        return "{}"

    r = pipeline(
        filter(screen(profile=PROFILE)),
        inspect(),
        filter(maf(min_metabolites=1)),
        score(PROFILE),
        describe(top=2),
        input=SearchResult([candidate("M1"),
                            candidate("M2", org="Mus musculus")]),
    ).run(llm=llm)
    assert isinstance(r["filter"], FilterResult)
    assert isinstance(r["inspect"], InspectResult)
    assert [c.study_id for c in r.inspect.candidates] == ["M1"]
    assert isinstance(r["score"], ScoreResult)
    assert isinstance(r["describe"], DescribeResult)
    assert list(r.describe.by_study) == ["M1"]
    assert calls["llm"] == 1
    assert r.describe.reused["M1"] is False
    assert "M1" in r.describe.fmt()


def test_run_custom_predicate():
    register_predicate(
        "min_samples", InspectResult,
        lambda cands, params: (
            [c for c in cands if (c.sample_count or 0) >= params["n"]],
            [(c, f"samples<{params['n']}") for c in cands
             if (c.sample_count or 0) < params["n"]],
            None))
    r = pipeline(
        filter(custom("min_samples", n=5)),
        score(),
        input=InspectResult([deep_candidate("A", samples=8),
                             deep_candidate("B", samples=4)]),
    ).run()
    assert [c.study_id for c in r.filter.survivors] == ["A"]
    assert r.filter.dropped[0][1] == "samples<5"


def test_run_describe_store_reuse(monkeypatch, tmp_path):
    monkeypatch.setattr(mtbls_agent.inspector, "inspect_studies", fake_inspect)
    root = tmp_path / "c"
    calls = {"llm": 0}

    def llm(prompt):
        calls["llm"] += 1
        return "{}"

    p = pipeline(
        filter(screen(profile=PROFILE)),
        inspect(),
        score(PROFILE),
        describe(top=1, cache=CacheOpts(enabled=False)),  # force body path
        input=SearchResult([candidate("M1")]),
    ).cache(root)

    r1 = p.run(llm=llm)
    assert calls["llm"] == 1            # first: generated
    r2 = p.run(llm=llm)                 # second run: store hit
    assert calls["llm"] == 1            # no new LLM call
    assert r2.describe.reused["M1"] is True
    assert (root / "sentences.json").exists()


def test_run_type_checked_input():
    with pytest.raises(TypeError, match="SearchResult"):
        pipeline(search("x"), input=FilterResult([], [])).run()


# ── cache ───────────────────────────────────────────────────────


def test_cache_replays_warm_steps(monkeypatch, tmp_path):
    root = tmp_path / "c"
    body_calls = {"n": 0}

    def counting(*a, **k):
        body_calls["n"] += 1
        return [candidate("M1")]

    monkeypatch.setattr("mtbls_agent.searcher.search_studies", counting)
    p1 = pipeline(search("urine")).cache(root)
    r1 = p1.run()
    assert body_calls["n"] == 1

    p2 = pipeline(search("urine")).cache(root)
    r2 = p2.run()                       # same config + input → replay
    assert body_calls["n"] == 1
    assert r1["search"].digest() == r2["search"].digest()
    assert (root / "plan.json").exists()

    p3 = pipeline(search("saliva")).cache(root)   # different query → miss
    p3.run()
    assert body_calls["n"] == 2


def test_cache_ttl_expiry(monkeypatch, tmp_path):
    root = tmp_path / "c"
    n = {"n": 0}

    def counting(*a, **k):
        n["n"] += 1
        return [candidate("M1")]

    monkeypatch.setattr("mtbls_agent.searcher.search_studies", counting)
    step = search("urine", cache=CacheOpts(ttl="0s"))
    pipeline(step).cache(root).run()
    assert n["n"] == 1
    pipeline(step).cache(root).run()    # stale → re-execute
    assert n["n"] == 2


def test_parse_ttl():
    assert parse_ttl("7d").days == 7
    assert parse_ttl("12h").seconds == 43200
    assert parse_ttl(None) is None
    assert parse_ttl("") is None
    with pytest.raises(ValueError):
        parse_ttl("bogus")


def test_cache_store_lookup_ttl(tmp_path):
    cs = CacheStore(tmp_path)
    r = SearchResult(candidates=[candidate("M1")], query="q")
    key = cs.key("search", {"query": "q"}, "init")
    cs.store("search", key, r, ttl=None)
    assert cs.lookup("search", key, None) is not None
    assert cs.lookup("search", key, "0s") is None


def test_plan_records_kind_and_config(monkeypatch, tmp_path):
    monkeypatch.setattr("mtbls_agent.searcher.search_studies",
                        lambda *a, **k: [candidate("M1")])
    root = tmp_path / "c"
    pipeline(search("urine")).cache(root).run()
    plan = json.loads((root / "plan.json").read_text())
    assert plan[0]["kind"] == "search"
    assert plan[0]["config"]["query"] == "urine"
    assert plan[0]["key"]


def test_force_refresh_writes_no_junk_key(monkeypatch, tmp_path):
    """force=True must not write a stray results/.json (zoo bug)."""
    root = tmp_path / "c"

    def counting(*a, **k):
        return [candidate("M1")]

    monkeypatch.setattr("mtbls_agent.searcher.search_studies", counting)
    p = pipeline(search("urine")).cache(root)
    p.run()
    p.run(force=True)
    junk = [f for f in (root / "results").iterdir() if f.name.startswith(".")]
    assert junk == []
    assert len(list((root / "results").iterdir())) == 1   # one key only


def test_force_refresh(monkeypatch, tmp_path):
    root = tmp_path / "c"
    n = {"n": 0}

    def counting(*a, **k):
        n["n"] += 1
        return [candidate("M1")]

    monkeypatch.setattr("mtbls_agent.searcher.search_studies", counting)
    p = pipeline(search("urine")).cache(root)
    p.run()
    assert n["n"] == 1
    p.run()                              # replay
    assert n["n"] == 1
    p.run(force=True)                    # bypass cache
    assert n["n"] == 2


def test_results_cross_steps_by_value(monkeypatch, tmp_path):
    """inspect mutates its input candidates in place — upstream results must
    stay pristine (fresh digests == warm-cache digests; no shared objects)."""
    import mtbls_agent.inspector

    def mutating_inspect(cands, **k):
        for c in cands:                       # mimics _merge_enriched
            c.sample_count = 999
            c.investigation_file_parsed = True
        return cands

    def fake_search(*a, **k):
        return [candidate("M1"), candidate("M2")]

    monkeypatch.setattr("mtbls_agent.searcher.search_studies", fake_search)
    monkeypatch.setattr(mtbls_agent.inspector, "inspect_studies",
                        mutating_inspect)
    root = tmp_path / "c"
    p = pipeline(
        search("urine"),
        filter(screen(profile=PROFILE)),
        inspect(),
        score(PROFILE),
    ).cache(root)
    r1 = p.run()
    assert r1["search"].candidates[0].sample_count == 8   # stayed shallow
    assert not r1["search"].candidates[0].investigation_file_parsed
    assert r1["inspect"].candidates[0].sample_count == 999  # deep on inspect
    # no object sharing between stored results
    assert r1["search"].candidates[0] is not r1["inspect"].candidates[0]
    # warm replay → identical digests
    r2 = p.run()
    assert r1["search"].digest() == r2["search"].digest()
    assert r1["filter"].digest() == r2["filter"].digest()


def test_pipeline_result_mapping():
    r = pipeline(
        filter(screen(profile=PROFILE)),
        input=SearchResult([candidate("M1")]),
    ).run()
    assert r["filter"] is r.filter
    assert r.order == ["filter"]
    assert isinstance(r.latest(FilterResult), FilterResult)
    assert "filter" in r.keys()