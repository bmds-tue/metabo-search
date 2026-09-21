"""Regression tests for the bugs found by the first adversarial audit (PROBLEMS.md).

Each test below asserts the DESIRED behavior; almost all fail against the
code as it was when ``PROBLEMS.md`` was written (that is the point — they pin
the bugs).  The fix plan lives in ``docs/fix-plan-audit.md``; each test names
the problem number it tracks.

All tests are offline/deterministic: the workbench runs on the fixture
corpus (conftest sets ``METABO_WORKBENCH_FIXTURES``), and the MetaboLights
inspector is stubbed where a run would otherwise hit the network.
"""

from __future__ import annotations

import json

import pytest

import metabo_search.downloader as dl_mod
import metabo_search.inspector as insp_mod
from conftest import FIXTURES_DIR

from metabo_search import (
    FilterResult,
    InspectResult,
    RequirementProfile,
    SampleSentencesStore,
    StudyRequirements,
    collect_sample_contexts,
    describe,
    download,
    harvest,
    filter,
    inspect,
    load_samples,
    load_study_from_isa,
    pipeline,
    prepare_samples,
    quick_probe,
    score,
    screen,
    search,
    submit_samples,
)
from metabo_search.core.cache import parse_ttl
from metabo_search.core.results import ScoreResult
from metabo_search.core.steps import _do_describe, _do_download
from metabo_search.models import OntologyTerm, StudyCandidate
from metabo_search.repositories.workbench.inspect import (
    deep_metadata as wb_deep_metadata,
)
from metabo_search.sample_gen import _get_char
from metabo_search.scorer import FitnessScore, ScoredCandidate

PROFILE = RequirementProfile(
    hard=StudyRequirements(organisms=["Homo sapiens"]))

VALID_RECIPE = json.dumps({
    "codes": {"alz": "Alzheimer's disease"},
    "sentence_template": "Patient {disease} from {organism}",
    "slot_sources": {"disease": {"type": "code", "field": "name"},
                     "organism": {"type": "organism"}},
})


# ── helpers (offline) ─────────────────────────────────────────────


def metabolights_candidate(sid: str = "MTBLS1") -> StudyCandidate:
    """A deep metabolights-style candidate with ISA-ish sample sheet."""
    c = StudyCandidate(
        study_id=sid, title=f"Study {sid}",
        organisms=[OntologyTerm(term="Homo sapiens")], sample_count=8)
    c.investigation_file_parsed = True
    c.sample_file_parsed = True
    c.sample_metadata = [
        {"Sample Name": "ALZ001",
         "Characteristics[Organism]": "Homo sapiens",
         "Characteristics[Organism part]": "urine"},
    ]
    c.sample_file_map = {"ALZ001": {"raw": ["FILES/RAW/ALZ_1.raw"],
                                    "derived": []}}
    return c


def _wb_loader(study_id: str, kind: str) -> dict:
    return json.loads((FIXTURES_DIR / f"study_{study_id}_{kind}.json").read_text())


def deep_workbench(sid: str = "ST000001") -> StudyCandidate:
    """Deep workbench candidate from the ST000001 fixtures (24 samples)."""
    c = StudyCandidate(study_id=sid, title=sid,
                       repository="metabolomics_workbench")
    return wb_deep_metadata([c], workers=2, load_payload=_wb_loader)[0]


def _describe_cfg(top: int = 3) -> type:
    return type("_Cfg", (), {"top": top, "revision": 0, "store": None})


# ── PROBLEM #1: download()/harvest() constraint gate leaks ─────────


@pytest.mark.parametrize("kwargs", [
    {"categories": []},
    {"file_types": []},
    {"sample_names": []},
    {"max_files": 0},
    {"max_size_gb": 0.0},
])
def test_download_empty_or_zero_constraints_rejected(kwargs):
    """Empty buckets / falsy numbers are NOT constraints — must be refused."""
    p = pipeline(
        download(dest_dir=".", **kwargs),
        input=InspectResult([metabolights_candidate()]))
    with pytest.raises(ValueError, match="constraint"):
        p.validate()


@pytest.mark.parametrize("kwargs", [
    {"max_files": 0},
    {"categories": []},
    {"max_size_gb": 0.0},
    {"file_types": []},
    {"sample_names": []},
])
def test_harvest_rejects_empty_or_zero_constraints(kwargs):
    """harvest() must never silently download *everything*."""
    with pytest.raises(ValueError, match="constraint"):
        harvest("q", download_kwargs=kwargs)


# ── PROBLEM #2: run(input=...) midstream continuation broken ──────


def test_run_midstream_score_with_runtime_input():
    """pipeline(score()).run(input=InspectResult) — the SKILL.md example."""
    insp = InspectResult([metabolights_candidate()])
    r = pipeline(score(PROFILE)).run(input=insp)
    assert len(r.score.ranked) == 1


def test_run_midstream_inspect_with_runtime_input(monkeypatch):
    """pipeline(inspect(), score()).run(input=FilterResult) — slice+extend."""
    monkeypatch.setattr(insp_mod, "inspect_studies",
                        lambda cands, **kw: cands)
    fr = FilterResult(survivors=[metabolights_candidate()], stage="shallow")
    r = pipeline(inspect(workers=1), score(PROFILE)).run(input=fr)
    assert [c.study_id for c in r.inspect.candidates] == ["MTBLS1"]
    assert len(r.score.ranked) == 1


# ── PROBLEM #3: workbench candidates yield no sample sentences ─────


def test_workbench_deep_candidate_yields_sample_contexts():
    deep = deep_workbench()
    assert len(deep.sample_metadata) == 24   # the fixture has 24 samples
    ctxs = collect_sample_contexts(deep)
    assert len(ctxs) == 24                   # currently 0 → bug


def test_workbench_describe_produces_sentences(tmp_path):
    deep = deep_workbench()
    store = SampleSentencesStore(str(tmp_path / "s.json"))
    task = prepare_samples(deep, store)
    descs = submit_samples(task, json.dumps({
        "codes": {}, "sentence_template": "A sample", "slot_sources": {}}))
    assert len(descs) == 24                  # currently [] → bug
    assert all(d.sentence for d in descs)


# ── PROBLEM #4: _get_char substring match (organism == tissue) ─────


@pytest.mark.parametrize("row", [
    # insertion order must not decide the winner (real ISA sheets vary)
    {"Characteristics[Organism part]": "urine",
     "Characteristics[Organism]": "Homo sapiens"},
    {"Characteristics[Organism]": "Homo sapiens",
     "Characteristics[Organism part]": "urine"},
])
def test_get_char_organism_not_matched_by_organism_part(row):
    assert _get_char(row, "Organism") == "Homo sapiens"
    assert _get_char(row, "Organism part") == "urine"


def test_get_char_organism_part_only_leaves_organism_empty():
    row = {"Characteristics[Organism part]": "urine"}
    assert _get_char(row, "Organism") == ""          # currently "urine"
    assert _get_char(row, "Organism part") == "urine"


def test_contexts_organism_and_tissue_are_distinct():
    c = metabolights_candidate()
    ctx = collect_sample_contexts(c)[0]
    assert ctx.organism == "Homo sapiens"            # currently "urine"
    assert ctx.tissue == "urine"


# ── PROBLEM #5: download()/describe() ignore the repository seam ───


def test_download_workbench_candidate_fails_loudly(tmp_path, monkeypatch):
    """Workbench has no download support → loud failure, never a silent 0."""
    calls = []
    monkeypatch.setattr(dl_mod, "list_data_files",
                        lambda *a, **k: calls.append(1) or [])
    step = download(categories=["raw"], dest_dir=str(tmp_path))
    insp = InspectResult([deep_workbench()])
    with pytest.raises(NotImplementedError, match="workbench"):
        _do_download(step.config, insp, files_cache_dir=None)
    assert calls == []         # the MetaboLights downloader must not run


def test_describe_skips_llm_for_study_without_contexts():
    """No contexts → no wasted LLM call, and an honest empty result."""
    empty = StudyCandidate(study_id="ST000099",
                           repository="metabolomics_workbench",
                           investigation_file_parsed=True)
    sr = ScoreResult(ranked=[ScoredCandidate(candidate=empty,
                                             score=FitnessScore())],
                     table=None)
    calls = []
    out = _do_describe(_describe_cfg(top=1), sr,
                       lambda prompt: calls.append(1) or "{}", None)
    assert calls == []               # currently 1 wasted call
    assert out.by_study == {}        # nothing pretended to be described


# ── PROBLEM #6: offline/fixtures mode hits the live metstat API ────


def test_workbench_search_offline_when_metstat_unavailable(monkeypatch):
    """No network → corpus backstop must still return candidates."""
    import metabo_search.repositories.workbench.client as wbclient
    from metabo_search.repositories.workbench.corpora import (
        load_corpus_fixtures,
    )
    from metabo_search.repositories.workbench.search import search_workbench
    from metabo_search.repositories.workbench.vocab import Vocab

    def no_network(slots):
        raise RuntimeError("offline")

    monkeypatch.setattr(wbclient, "metstat", no_network)
    corpus = load_corpus_fixtures(FIXTURES_DIR)
    vocab = Vocab.from_corpus(corpus)
    # disease-only: still triggers the confident DISEASE slot (fast path was
    # intended), but no organism screen to keep the fixture data honest.
    prof = RequirementProfile(hard=StudyRequirements(diseases=["diabetes"]))
    cands, meta = search_workbench("diabetes serum", profile=prof,
                                   corpus=corpus, vocab=vocab,
                                   max_results=10)
    assert cands                      # currently hard-raise
    assert meta["fast_path"] is False
    assert "unavailable" in meta["notice"]


def test_workbench_empty_metstat_pool_falls_back_to_corpus(monkeypatch):
    """metstat returns a pool that doesn't intersect the corpus → the search
    must not silently return 0 with no notice."""
    import metabo_search.repositories.workbench.client as wbclient
    from metabo_search.repositories.workbench.corpora import (
        load_corpus_fixtures,
    )
    from metabo_search.repositories.workbench.search import search_workbench
    from metabo_search.repositories.workbench.vocab import Vocab

    monkeypatch.setattr(wbclient, "metstat",
                        lambda slots: {"Row1": {"study": "ST999999"}})
    corpus = load_corpus_fixtures(FIXTURES_DIR)
    vocab = Vocab.from_corpus(corpus)
    prof = RequirementProfile(hard=StudyRequirements(diseases=["diabetes"]))
    cands, meta = search_workbench("diabetes serum", profile=prof,
                                   corpus=corpus, vocab=vocab,
                                   max_results=10)
    assert cands                      # currently [] silently
    assert meta["notice"] is not None  # and tells the agent why


# ── PROBLEM #7: malformed LLM recipe -> raw crash + junk cached ────


def _recipe_task(tmp_path):
    store = SampleSentencesStore(str(tmp_path / "s.json"))
    return prepare_samples(metabolights_candidate(), store)


def test_submit_samples_non_json_raises_clear_error(tmp_path):
    task = _recipe_task(tmp_path)
    with pytest.raises(ValueError, match="JSON"):
        submit_samples(task, "this is not json")


def test_submit_samples_array_json_raises(tmp_path):
    task = _recipe_task(tmp_path)
    with pytest.raises(ValueError, match="di[A-Zc]t|object|recipe"):
        submit_samples(task, "[1, 2, 3]")


def test_submit_samples_empty_template_rejected_and_not_cached(tmp_path):
    task = _recipe_task(tmp_path)
    with pytest.raises(ValueError, match="sentence_template"):
        submit_samples(task, json.dumps({"codes": {}, "slot_sources": {}}))
    assert load_samples(task) is None      # junk must not be cached forever


def test_submit_samples_failure_does_not_pollute_cache(tmp_path):
    task = _recipe_task(tmp_path)
    with pytest.raises(ValueError):
        submit_samples(task, "garbage")
    assert load_samples(task) is None


def test_submit_samples_valid_recipe_still_caches(tmp_path):
    task = _recipe_task(tmp_path)
    descs = submit_samples(task, VALID_RECIPE)
    assert descs and load_samples(task) == descs


# ── PROBLEM #8: zero/negative limits silently destroy results ──────


@pytest.mark.parametrize("n", [0, -1])
def test_screen_zero_or_negative_min_survivors_rejected(n):
    p = pipeline(search("x"), filter(screen(PROFILE, min_survivors=n)))
    with pytest.raises(ValueError, match="min_survivors"):
        p.validate()


def test_describe_negative_top_rejected():
    p = pipeline(
        search("x"), inspect(), score(PROFILE), describe(top=-1))
    with pytest.raises(ValueError, match="top"):
        p.validate()

# (Note: a 0-duration TTL like "0s"/"0d" is NOT rejected — it is the
# codebase's established "always stale" idiom, deliberately exercised by
# test_cache_ttl_expiry and test_cache_store_lookup_ttl.)


# ── PROBLEM #9: minors ────────────────────────────────────────────


def test_quick_probe_accepts_cache_root(tmp_path):
    """quick_probe should be cacheable in the same style as quick_discovery."""
    p = quick_probe("x", profile=PROFILE, cache_root=str(tmp_path))
    assert p.cache_root is not None


def test_isa_with_only_investigation_file_is_shallow(tmp_path):
    """An Investigation-only ISA dir must not pass for a deep inspection."""
    (tmp_path / "i_Investigation.txt").write_text(
        "Comment[MTBLS ID]\tMTBLS9999\nStudy Description\tx\n",
        encoding="utf-8")
    c = load_study_from_isa("MTBLS9999", str(tmp_path))
    assert c.inspection_depth == "shallow"      # currently "deep"


def test_describe_top_zero_without_llm_ok():
    """describe(top=0) describes nothing → must not demand an LLM."""
    sr = ScoreResult(ranked=[ScoredCandidate(
        candidate=metabolights_candidate(), score=FitnessScore())],
        table=None)
    out = _do_describe(_describe_cfg(top=0), sr, llm=None, store_path=None)
    assert len(out.by_study) == 0