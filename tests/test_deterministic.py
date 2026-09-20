"""Deterministic discovery: server-side args + shallow screening."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from metabo_search.models import RequirementProfile, StudyRequirements, StudyCandidate
from metabo_search.searcher import profile_to_search_args
from metabo_search.scorer import screen_candidates


def _mk(sid, organism="Homo sapiens", count=100, raw=50, techs=("LC-MS",),
        desc="lipidomics of human plasma", parts=("blood plasma",),
        design=("lipidomics",)):
    c = StudyCandidate(study_id=sid)
    from metabo_search.models import OntologyTerm
    c.organisms = [OntologyTerm(term=organism)]
    c.organism_parts = [OntologyTerm(term=p) for p in parts]
    c.sample_count = count
    c.raw_file_count = raw
    c.assay_techniques = [{"name": t} for t in techs]
    c.design_descriptors = [OntologyTerm(term=d) for d in design]
    c.title = desc
    c.description = desc
    return c


def test_profile_to_search_args_maps_hard():
    p = RequirementProfile(
        hard=StudyRequirements(organisms=["Homo sapiens"], techniques=["LC-MS"],
                               sample_types=["blood plasma"], min_samples=30))
    a = profile_to_search_args(p)
    assert a["organism"] == ["Homo sapiens"]
    assert a["technique"] == ["LC-MS"]
    assert a["sample_type"] == ["blood plasma"]
    assert a["min_samples"] == 30


def test_screen_drops_hard_fail_and_sorts():
    ok  = _mk("OK1", organism="Homo sapiens", count=200)
    wrong_org = _mk("WO", organism="Mus musculus")
    too_few = _mk("TF", count=5)
    no_raw  = _mk("NR", raw=0)
    wrong_tech = _mk("WT", techs=("NMR",))
    cands = [ok, wrong_org, too_few, no_raw, wrong_tech]
    p = RequirementProfile(hard=StudyRequirements(
        organisms=["Homo sapiens"], min_samples=50, has_raw_data=True,
        techniques=["LC-MS"]))
    r = screen_candidates(cands, p, min_survivors=10)
    assert {c.study_id for c in r.survivors} == {"OK1"}
    reasons = {sid: reason for sid, reason in [(c.study_id, reason) for c, reason in r.dropped]}
    assert "WO" in reasons and "TF" in reasons and "NR" in reasons and "WT" in reasons


def test_screen_ignores_ionization_and_formats_shallow():
    """Deep-only criteria (ionization, formats) must NOT hard-fail the screen."""
    c = _mk("ION", count=80)
    p = RequirementProfile(hard=StudyRequirements(
        organisms=["Homo sapiens"], ionization_modes=["positive"],
        data_formats=["mzML"]))
    r = screen_candidates([c], p, min_survivors=10)
    assert c.study_id in {x.study_id for x in r.survivors}, \
        "ionization/data-formats screened shallowly would be a false hard-fail"


def test_screen_ranks_by_shallow_soft_score():
    high = _mk("HI", organism="Homo sapiens", count=500,
               desc="targeted lipidomics of human blood plasma in positive mode")
    low  = _mk("LO", organism="Homo sapiens", count=5)  # low count, no free-text match
    p = RequirementProfile(
        nice_to_have=StudyRequirements(min_samples=300),
        free_text="targeted lipidomics human blood plasma positive")
    r = screen_candidates([low, high], p, min_survivors=10)
    assert r.survivors[0].study_id == "HI", "shallow rank should prefer the better match"
