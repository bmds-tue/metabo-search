"""Workbench search — corpus backstop (deterministic) + notice contract."""


from conftest import FIXTURES_DIR

from metabo_search.models import RequirementProfile, StudyRequirements
from metabo_search.repositories.workbench.corpora import load_corpus_fixtures
from metabo_search.repositories.workbench.search import (
    build_notice,
    corpus_rank,
    search_workbench,
)
from metabo_search.repositories.workbench.slots import assemble_slots
from metabo_search.repositories.workbench.vocab import Vocab

CORPUS = load_corpus_fixtures(FIXTURES_DIR)
VOCAB = Vocab.from_corpus(CORPUS)


def test_corpus_rank_is_deterministic():
    a = corpus_rank({"serum"}, CORPUS)
    b = corpus_rank({"serum"}, CORPUS)
    assert [(m.study_id, m.score) for m in a] == \
        [(m.study_id, m.score) for m in b]
    assert a and all(m.matched_tokens == ["serum"] for m in a)


def test_corpus_rank_requires_overlap():
    assert corpus_rank({"zzzzz"}, CORPUS) == []


def test_search_workbench_returns_tagged_candidates():
    cands, meta = search_workbench(
        "serum metabolomics", corpus=CORPUS, vocab=VOCAB, max_results=10)
    assert cands
    assert all(c.repository == "metabolomics_workbench" for c in cands)
    assert all(c.study_id.startswith("ST") for c in cands)
    assert all(c.title for c in cands)
    assert meta["matched"] == len(cands)
    assert meta["fast_path"] is False


def test_search_with_min_samples_filter():
    cands, _ = search_workbench(
        "serum", corpus=CORPUS, vocab=VOCAB, max_results=50,
        min_samples=1000)
    # every survivor must carry a numeric sample count ≥ 1000
    assert all((c.sample_count or 0) >= 1000 for c in cands)


def test_ambiguous_disease_produces_actionable_notice():
    # a canonical hit → no notice
    ok = assemble_slots(
        RequirementProfile(hard=StudyRequirements(
            diseases=["Alzheimers disease"])), VOCAB)
    assert build_notice(ok.decisions) is None

    # an off-topic / un-matchable term → abstain + actionable notice
    amb = assemble_slots(
        RequirementProfile(hard=StudyRequirements(
            diseases=["asdfghjkl"])), VOCAB)
    ns = build_notice(amb.decisions)
    assert ns is not None
    assert "none entered" in ns
    assert "metabolomics_workbench" in ns          # scoping hint
    assert "pipeline(input=SearchResult(" in ns    # composition step


def test_fast_path_pools_by_slots_and_ranks_by_text(monkeypatch):
    """metstat supplies the pool; free text ranks it, never starves it."""
    import metabo_search.repositories.workbench.client as wbclient

    def fake_metstat(slots):
        return {"Row1": {"study": "ST005186",   # serum-title study
                         "study_title": "Serum Metabolomics",
                         "species": "Human",
                         "source": "Blood", "disease": "Diabetes"}}

    monkeypatch.setattr(wbclient, "metstat", fake_metstat)
    prof = RequirementProfile(hard=StudyRequirements(
        diseases=["diabetes"], organisms=["Homo sapiens"],
        sample_types=["blood"]))
    cands, meta = search_workbench(
        "urine but not in title", profile=prof,
        corpus=CORPUS, vocab=VOCAB, max_results=10)
    sids = [c.study_id for c in cands]
    assert "ST005186" in sids        # pool member kept despite no token hit
    assert meta["fast_path"] is True
    assert meta["notice"] is None


def test_confident_profile_fills_disease_slot_and_notice_is_none():
    assembly = assemble_slots(
        RequirementProfile(hard=StudyRequirements(
            diseases=["diabetes"], organisms=["Homo sapiens"],
            sample_types=["blood"])), VOCAB)
    assert assembly.slots[5] == "Diabetes"          # DISEASE slot
    assert assembly.slots[3] == "Human"             # SPECIES slot
    assert assembly.slots[4] == "Blood"             # SOURCE slot
    assert assembly.used_fast_path
    assert build_notice(assembly.decisions) is None