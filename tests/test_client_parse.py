"""parse_hit must produce canonical, round-trip-stable candidates.

A real bug: ``factors`` was copied through as raw API dicts (camelCase keys)
while the field type is ``list[OntologyTerm]`` — fresh vs cached serializations
diverged, so warm-pipeline cache keys shifted and steps re-ran.
"""

from mtbls_agent.client import parse_hit
from mtbls_agent.core.cache import CacheStore
from mtbls_agent.core.results import SearchResult
from mtbls_agent.models import OntologyTerm, StudyCandidate
from mtbls_agent.searcher import search_studies

HIT = {
    "studyId": "MTBLSx",
    "title": "Factors study",
    "organisms": [{"term": "Homo sapiens", "termSourceRef": "NCBITAXON",
                   "termAccessionNumber": "9606"}],
    "organismParts": [{"term": "urine", "termSourceRef": "UBERON"}],
    "factors": [{"termSourceRef": "NCIT",
                 "termAccessionNumber": "http://purl.obolibrary.org/obo/NCIT_C20587",
                 "term": "Aging"}],
    "assayTechniques": [{"main": "MS", "sub": "LC", "name": "LC-MS",
                         "technique": "LC-MS"}],
    "designDescriptors": [{"term": "Aging", "termSourceRef": "NCIT",
                           "termAccessionNumber": "x/NCIT_C20587"}],
    "technologyTypes": [{"term": "mass spectrometry"}],
    "sampleCount": 10,
    "publications": [{"doi": "10.1/x", "pubMedId": "123", "title": "t",
                      "authorList": "a", "status": {"term": "published"}}],
}


def test_parse_hit_factors_are_canonical_ontology_terms():
    cand = StudyCandidate(**parse_hit(HIT))
    assert all(isinstance(f, OntologyTerm) for f in cand.factors)
    assert cand.factors[0].term_accession_number == \
        "http://purl.obolibrary.org/obo/NCIT_C20587"
    assert cand.factors[0].term == "Aging"
    assert cand.factors[0].term_source_ref == "NCIT"
    # adjacent fields stay canonical too
    assert isinstance(cand.organisms[0], OntologyTerm)
    assert isinstance(cand.design_descriptors[0], OntologyTerm)


def test_search_result_roundtrip_is_stable_with_camelcase_factors():
    cand = StudyCandidate(**parse_hit(HIT))
    r = SearchResult(candidates=[cand], query="x")
    assert r.to_json() == SearchResult.from_json(r.to_json()).to_json()
    assert r.digest() == SearchResult.from_json(r.to_json()).digest()


def test_cache_key_stable_across_roundtrip(tmp_path):
    cand = StudyCandidate(**parse_hit(HIT))
    r = SearchResult(candidates=[cand], query="x")
    cs = CacheStore(tmp_path)
    k1 = cs.key("search", {"query": "x"}, r.digest())
    k2 = cs.key("search", {"query": "x"},
                SearchResult.from_json(r.to_json()).digest())
    assert k1 == k2