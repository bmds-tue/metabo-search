"""Regression tests for the parallel-agent report (docs/parallel-agent-audit.md).

- 2.1  metstat flat record (exactly-one-study intersection) → normalized;
       ``metstat_matches`` must never touch a non-dict row.
- 2.2  matcher exact-value preference: ``cancer`` → ``Cancer``, never the
       fuzzy subtype ``Lung cancer`` (WRatio case-penalty + partial-boost).
- 2.3  transient server disconnects on metstat → bounded retry.

All offline/deterministic: workbench runs on fixtures (conftest), metstat is
monkeypatched, the normalization helper is pure.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import FIXTURES_DIR

from metabo_search import RequirementProfile, StudyRequirements
from metabo_search.repositories.workbench.client import _normalize_metstat
from metabo_search.repositories.workbench.matcher import match
from metabo_search.repositories.workbench.search import metstat_matches


# ── 2.1 metstat flat-record normalization ──────────────────────────


def test_normalize_metstat_flat_record_becomes_row1():
    flat = {"study": "ST003989", "study_title": "Plasma AA in lung cancer",
            "species": "Human", "source": "Blood", "disease": "Lung cancer"}
    out = _normalize_metstat(flat)
    assert set(out) == {"Row1"}
    assert out["Row1"] == flat


def test_normalize_metstat_row_table_passes_through():
    rows = {"Row1": {"study": "ST000001", "disease": "Diabetes"},
            "Row2": {"study": "ST000002", "disease": "Diabetes"}}
    assert _normalize_metstat(rows) == rows


def test_normalize_metstat_empty_and_list_forms():
    assert _normalize_metstat({}) == {}
    assert _normalize_metstat([]) == {}
    assert _normalize_metstat([{"study": "ST000001"},
                               {"study": "ST000002"}]) == {
        "Row1": {"study": "ST000001"}, "Row2": {"study": "ST000002"}}


def test_normalize_metstat_skips_non_record_values():
    assert _normalize_metstat({"Row1": "junk"}) == {}
    assert _normalize_metstat({"Row1": {"no_study": 1}}) == {}
    assert _normalize_metstat("not a dict") == {}


def test_metstat_matches_flat_record_defensive(monkeypatch):
    """Even if something bypasses the client normalization, metstat_matches
    must not crash on a flat record — it should read it as one study."""
    import metabo_search.repositories.workbench.client as wbclient

    monkeypatch.setattr(wbclient, "metstat",
                        lambda slots: {"study": "ST003989",
                                       "study_title": "x"})
    assert metstat_matches(("",) * 8) == {"ST003989"}


# ── 2.2 matcher exact-value preference ─────────────────────────────

CANCER_LIKE = ["Cancer", "Lung cancer", "Metabolic syndrome",
               "Breast cancer"]


def test_matcher_generic_term_prefers_exact_canonical():
    """'cancer' must resolve to 'Cancer' (exact), never the fuzzy subtype
    'Lung cancer' that WRatio's partial-ratio boost prefers."""
    r = match("cancer", CANCER_LIKE)
    assert r.value == "Cancer"
    assert r.confident
    assert r.score == 100.0


def test_matcher_exact_canonical_returns_itself():
    assert match("Lung cancer", CANCER_LIKE).value == "Lung cancer"
    assert match("Cancer", CANCER_LIKE).value == "Cancer"
    assert match("Breast cancer", CANCER_LIKE).value == "Breast cancer"


def test_matcher_exact_resolves_title_case():
    assert match("depression",
                 ["Depression", "Depressive disorder"]).value == "Depression"


def test_matcher_exact_resolves_coin_flip():
    """'brain' is exactly the canonical 'Brain' — the exact match supersedes
    the old tie-slack abstain (no more Bee Brain/Brain coin-flip)."""
    assert match("brain", ["Bee Brain", "Brain"]).value == "Brain"


def test_matcher_alias_still_beats_exact_elsewhere():
    """Alias semantics are preserved: 'plasma' → 'Blood' (the WB canonical),
    not some fuzzy-plasma value."""
    r = match("plasma", ["Blood", "Seminal plasma", "Umbilical cord plasma"])
    assert r.value == "Blood"
    assert r.by_alias is True


def test_matcher_no_exact_still_abstains_on_tie():
    """Without an exact canonical, genuine coin-flips still abstain."""
    assert match("fever", ["Valley fever", "Hay fever"]).value is None


# ── 2.3 metstat transient-failure retry ────────────────────────────


def test_metstat_retries_transient_disconnect(monkeypatch):
    """A server 'disconnected' on the first tries must not abort the search."""
    import metabo_search.repositories.workbench.client as wbclient

    monkeypatch.delenv("METABO_WORKBENCH_FIXTURES", raising=False)
    calls = {"n": 0}
    flat = {"study": "ST003989", "study_title": "x"}

    def flaky_get(path, **params):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.RemoteProtocolError("Server disconnected")
        return flat

    monkeypatch.setattr(wbclient, "get", flaky_get)
    out = wbclient.metstat(("",) * 8)
    assert out == {"Row1": flat}
    assert calls["n"] == 3


def test_metstat_gives_up_after_retries(monkeypatch):
    import metabo_search.repositories.workbench.client as wbclient

    monkeypatch.delenv("METABO_WORKBENCH_FIXTURES", raising=False)

    def always_fail(path, **params):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(wbclient, "get", always_fail)
    with pytest.raises(RuntimeError, match="metstat"):
        wbclient.metstat(("",) * 8)


def test_metstat_offline_fixture_mode_still_refuses():
    import metabo_search.repositories.workbench.client as wbclient

    # conftest sets METABO_WORKBENCH_FIXTURES → metstat refuses to go live
    with pytest.raises(RuntimeError, match="fixture"):
        wbclient.metstat(("",) * 8)


# ── end-to-end: search_workbench on a flat-record pool ─────────────


def test_search_workbench_flat_record_pool_fast_path(monkeypatch):
    """A flat-record metstat pool must flow through search_workbench as a
    normal pool (fast path preserved)."""
    import metabo_search.repositories.workbench.client as client_mod
    from metabo_search.repositories.workbench.corpora import (
        load_corpus_fixtures,
    )
    from metabo_search.repositories.workbench.search import search_workbench
    from metabo_search.repositories.workbench.vocab import Vocab

    monkeypatch.setattr(
        client_mod, "metstat",
        lambda slots: {"study": "ST005186", "study_title": "Serum Metabolomics",
                       "species": "Human", "source": "Blood",
                       "disease": "Lung cancer"})
    corpus = load_corpus_fixtures(FIXTURES_DIR)
    vocab = Vocab.from_corpus(corpus)
    prof = RequirementProfile(hard=StudyRequirements(diseases=["Cancer"]))
    cands, meta = search_workbench("serum", profile=prof,
                                   corpus=corpus, vocab=vocab,
                                   max_results=10)
    assert any(c.study_id == "ST005186" for c in cands)
    assert meta["fast_path"] is True