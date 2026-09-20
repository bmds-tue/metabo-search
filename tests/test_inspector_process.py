"""Process-pool parsing: real ISA parse identical in threads vs processes."""

import os

import pytest

from metabo_search import inspector as ins
from metabo_search.models import StudyCandidate

MINI_INVESTIGATION = """ONTOLOGY SOURCE REFERENCE
Term Source Name\tTerm Source File\tTerm Source Version\tTerm Source Description
NCBITAXON\t\thttp://purl.obolibrary.org/obo/ncbitaxon.owl\t

INVESTIGATION
Investigation Identifier\tInvestigation Title\tInvestigation Description\tSubmission Date\tPublic Release Date
{sid}\tMini study\tA fixture\t2020-01-01\t2020-01-01

INVESTIGATION CONTACTS
Investigation Person Last Name\tInvestigation Person First Name\tInvestigation Person Email
Doe\tJane\tj@x.org

STUDY DESIGN DESCRIPTORS
Design Type\tDesign Type Term Accession Number\tDesign Type Term Source REF
growth condition\t\thttp://purl.obolibrary.org/obo/OBI.owl

FACTORS
Study Factor Name\tStudy Factor Type\tStudy Factor Term Source REF
disease\tcharacteristics\t

STUDY ASSAYS
Study Assay File Name\tStudy Assay Measurement Type\tStudy Assay Technology Type\tStudy Assay Technology Platform
a_{sid}.txt\tmetabolite profiling\tmass spectrometry\tLC-MS

STUDY PROTOCOLS
Study Protocol Name\tStudy Protocol Type\tStudy Protocol Description
p1\tsample collection\t

STUDY FILES
Study File Name\tStudy File Type
{sid}.s_{sid}.txt\tsample
{sid}.a_{sid}.txt\tassay

SAMPLE FILE
{sid}.s_{sid}.txt

ASSAY FILE
{sid}.a_{sid}.txt
"""


def make_mini_study(tmp_path, sid: str, n_samples: int, maf: bool = False) -> None:
    d = tmp_path / sid / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "i_Investigation.txt").write_text(MINI_INVESTIGATION.format(sid=sid))
    rows = ["Source Name\tSample Name\tCharacteristics[Organism]\tSample Type"]
    for i in range(n_samples):
        rows.append(f"S{i+1}\tS{i+1}\tHomo sapiens\t{'QC' if i == 0 else 'patient'}")
    (d / f"s_{sid}.txt").write_text("\n".join(rows))
    (d / f"a_{sid}.txt").write_text(
        "Source Name\tSample Name\tAssay Name\tRaw Spectral Data File\n"
        f"S1\tS1\tA1\tFILES/S1_raw.mzML\n")


def test_process_parse_equals_threads(tmp_path, monkeypatch):
    """Same on-disk ISA, parsed in-thread vs in-process → identical results."""
    src = tmp_path / "src"
    sids = ["MTBLSa", "MTBLSb", "MTBLSc"]
    n_per = {"MTBLSa": 3, "MTBLSb": 5, "MTBLSc": 2}
    for s in sids:
        make_mini_study(src, s, n_per[s])

    def fake_download(sid, root, download_data_files):
        return {"path": str(src / sid / sid), "data_files": []}

    monkeypatch.setattr(ins, "_download_one", fake_download)
    cands = [StudyCandidate(study_id=s) for s in sids]
    th = ins.inspect_studies(cands, max_workers=3, tmp_dir=str(tmp_path / "t"),
                             parse_workers=0)
    pr = ins.inspect_studies(cands, max_workers=3, tmp_dir=str(tmp_path / "p"),
                             parse_workers=2)
    for a, b in zip(th, pr):
        assert a.study_id == b.study_id
        assert a.sample_metadata == b.sample_metadata
        assert len(a.sample_metadata) == n_per[a.study_id]
        assert a.investigation_file_parsed == b.investigation_file_parsed
        assert a.sample_file_parsed == b.sample_file_parsed
        assert a.metabolite_count == b.metabolite_count
        assert a.data_files == b.data_files


def test_process_fallback_to_threads_when_spawn_unavailable(monkeypatch, tmp_path):
    """ProcessPoolExecutor failure → graceful thread fallback, same results."""
    src = tmp_path / "src"
    sids = ["MTBLSa", "MTBLSb"]
    for s in sids:
        make_mini_study(src, s, 2)

    def fake_download(sid, root, download_data_files):
        return {"path": str(src / sid / sid), "data_files": []}

    monkeypatch.setattr(ins, "_download_one", fake_download)
    monkeypatch.setattr(ins, "ProcessPoolExecutor", _BoomPool)
    cands = [StudyCandidate(study_id=s) for s in sids]
    out = ins.inspect_studies(cands, max_workers=2, tmp_dir=str(tmp_path / "t"),
                              parse_workers=2)
    assert all(c.sample_file_parsed for c in out)


class _BoomPool:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        raise RuntimeError("no spawn for you")

    def __exit__(self, *a):
        pass

    def submit(self, *a, **k):
        raise AssertionError("must not reach submit")


def test_resolve_parse_workers_rules(monkeypatch):
    assert ins._resolve_parse_workers(0, 20) == 0
    assert ins._resolve_parse_workers(4, 20) == 4
    assert ins._resolve_parse_workers(None, 3) == 0          # too few
    monkeypatch.setattr(ins.os, "cpu_count", lambda: 8)
    assert ins._resolve_parse_workers(None, 20) == 8
    assert ins._resolve_parse_workers(None, 8) == 8          # at threshold
    monkeypatch.setenv("MTBLS_PARSE_PROCESSES", "0")
    assert ins._resolve_parse_workers(None, 20) == 0
    monkeypatch.delenv("MTBLS_PARSE_PROCESSES")
    monkeypatch.setenv("MTBLS_PARSE_PROCESSES", "3")
    assert ins._resolve_parse_workers(None, 20) == 3