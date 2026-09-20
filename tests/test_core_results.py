"""Phase 1: core Result types — round-trip, digest stability, fmt."""

from mtbls_agent.core.results import (
    SearchResult, FilterResult, InspectResult, ScoreResult,
    DescribeResult, DownloadResult, ExportResult,
)
from mtbls_agent.models import (
    AssayInfo, ComparisonReport, DataFileInfo, FitnessScore,
    OntologyTerm, ProtocolInfo, PublicationInfo, RequirementProfile,
    ScoredCandidate, StudyCandidate, StudyRequirements,
)
from mtbls_agent.sample_gen import SampleDescription


def make_deep_candidate(study_id="MTBLS1", title="Deep study") -> StudyCandidate:
    c = StudyCandidate(
        study_id=study_id,
        title=title,
        description="a study",
        status="public",
        organisms=[OntologyTerm("Homo sapiens", "NCBITAXON", "9606")],
        assay_techniques=[{"term": "LC-MS", "accession": "x"}],
        sample_count=12,
        raw_file_count=3,
        assays=[AssayInfo(technique_name="LC-MS", ionization_mode="positive",
                          raw_file_count=2, column_type="C18")],
        data_files=[DataFileInfo(path="FILES/RAW/a.raw", size_bytes=100)],
        protocols=[ProtocolInfo(name="profiling", protocol_type="extraction")],
        publications=[PublicationInfo(doi="10.1000/xyz", title="paper")],
        sample_metadata=[{"Sample Name": "S1", "Organism": "human"}],
        sample_file_map={"S1": {"raw": ["FILES/RAW/S1.raw"], "derived": []}},
        metabolite_count=42,
        metadata_completeness=0.9,
        investigation_file_parsed=True,
        assay_files_parsed=True,
        sample_file_parsed=True,
        maf_files_parsed=True,
    )
    c._raw_api_result = {"huge": {"blob": "ignored"}}   # must be excluded
    return c


def test_search_roundtrip_and_digest_stability():
    r = SearchResult(candidates=[StudyCandidate(study_id="MTBLS1", title="A")],
                     query="urine", args_used={"organism": "Homo sapiens"})
    r2 = SearchResult.from_json(r.to_json())
    assert r2.digest() == r.digest()
    assert r2.query == "urine"
    assert isinstance(r2.candidates[0], StudyCandidate)
    assert r2.candidates[0].study_id == "MTBLS1"
    assert "MTBLS1" in r.fmt()


def test_deep_candidate_roundtrip_preserves_everything():
    c = make_deep_candidate()
    r = InspectResult(candidates=[c], isa_dirs={"MTBLS1": "/tmp/isa/MTBLS1"})
    r2 = InspectResult.from_json(r.to_json())
    assert r2.digest() == r.digest()
    c2 = r2.candidates[0]
    assert c2.assay_files_parsed and c2.maf_files_parsed
    assert c2.metabolite_count == 42
    assert c2.sample_file_map["S1"]["raw"] == ["FILES/RAW/S1.raw"]
    assert isinstance(c2.assays[0], AssayInfo)
    assert c2.assays[0].ionization_mode == "positive"
    assert isinstance(c2.organisms[0], OntologyTerm)
    assert c2.organisms[0].term_source_ref == "NCBITAXON"
    assert isinstance(c2.data_files[0], DataFileInfo)
    assert isinstance(c2.protocols[0], ProtocolInfo)
    assert isinstance(c2.publications[0], PublicationInfo)
    assert c2.sample_metadata == [{"Sample Name": "S1", "Organism": "human"}]
    assert c2.inspection_depth == "deep"


def test_raw_api_result_excluded():
    c = make_deep_candidate()
    r1 = InspectResult(candidates=[c])
    c_no_raw = make_deep_candidate()
    r2 = InspectResult(candidates=[c_no_raw])
    # presence of _raw_api_result neither serializes nor changes digest
    assert r1.to_dict()["candidates"][0] is not None
    assert "huge" not in r1.to_json()
    assert r1.digest() == r2.digest()
    restored = InspectResult.from_json(r1.to_json()).candidates[0]
    assert restored._raw_api_result == {}


def test_digest_changes_with_content():
    a = SearchResult(candidates=[StudyCandidate(study_id="MTBLS1", title="A")])
    b = SearchResult(candidates=[StudyCandidate(study_id="MTBLS2", title="A")])
    c = SearchResult(candidates=[StudyCandidate(study_id="MTBLS1", title="B")])
    assert a.digest() != b.digest()
    assert a.digest() != c.digest()


def test_filter_result_with_dropped_tuples():
    survivors = [StudyCandidate(study_id="MTBLS1", title="A")]
    dropped = [(StudyCandidate(study_id="MTBLS2", title="B"), "no MAF")]
    r = FilterResult(survivors=survivors, dropped=dropped, order={"MTBLS1": 0.9})
    r2 = FilterResult.from_json(r.to_json())
    assert r2.digest() == r.digest()
    assert r2.dropped[0][0].study_id == "MTBLS2"
    assert r2.dropped[0][1] == "no MAF"
    assert r2.order["MTBLS1"] == 0.9
    assert "1 dropped" in r.fmt()


def test_score_result_roundtrip_with_table():
    c = make_deep_candidate()
    sc = ScoredCandidate(candidate=c, score=FitnessScore(
        overall=0.85, hard_passed=True, hard_fail_reasons=[],
        per_criterion={"organisms": 1.0}, criterion_explanations={}))
    table = ComparisonReport(
        candidates=[sc],
        table_columns=["study_id", "score", "hard_pass"],
        table_rows=[["MTBLS1", 0.85, True]],
        query_profile=RequirementProfile(
            hard=StudyRequirements(organisms=["Homo sapiens"], min_samples=5),
            nice_to_have=StudyRequirements(techniques=["LC-MS"]),
            free_text="urine"),
    )
    r = ScoreResult(ranked=[sc], table=table)
    r2 = ScoreResult.from_json(r.to_json())
    assert r2.digest() == r.digest()
    r2sc = r2.ranked[0]
    assert r2sc.study_id == "MTBLS1"
    assert abs(r2sc.score.overall - 0.85) < 1e-9
    assert r2sc.score.hard_passed is True
    assert r2.table.query_profile.hard.organisms == ["Homo sapiens"]
    assert r2.table.query_profile.hard.min_samples == 5
    assert r2.table.table_rows[0] == ["MTBLS1", 0.85, True]
    assert "MTBLS1" in r.fmt()
    assert "✓" in r.fmt()


def test_describe_result_roundtrip():
    descs = [SampleDescription(study_id="MTBLS1", sample_name="S1",
                               sentence="Urine from Alzheimer's patient.",
                               used_sources=["disease"])]
    r = DescribeResult(by_study={"MTBLS1": descs}, revision=1,
                       reused={"MTBLS1": True})
    r2 = DescribeResult.from_json(r.to_json())
    assert r2.digest() == r.digest()
    assert isinstance(r2.by_study["MTBLS1"][0], SampleDescription)
    assert r2.by_study["MTBLS1"][0].sentence.startswith("Urine")
    assert "cached" in r.fmt(detail=True)


def test_download_export_roundtrip():
    d = DownloadResult(dest_dir="/tmp/d", downloaded=["MTBLS1/a.raw"],
                       total_bytes=1024, failed=[])
    d2 = DownloadResult.from_json(d.to_json())
    assert d2.digest() == d.digest()
    assert d2.total_bytes == 1024
    assert "MB" in d.fmt()

    e = ExportResult(path="out.csv", rows=3, columns=["a", "b"])
    e2 = ExportResult.from_json(e.to_json())
    assert e2.digest() == e.digest()
    assert e2.rows == 3