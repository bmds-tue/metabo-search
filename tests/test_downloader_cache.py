"""FILES/ listing disk cache: repeated list_data_files skips the HTTP walk."""

import dataclasses
import json

from mtbls_agent.core.results import InspectResult
from mtbls_agent.downloader import DataFileRef, list_data_files
from mtbls_agent.models import StudyCandidate

CANNED = [
    DataFileRef(relative_path="FILES/RAW/a.raw", size_bytes=1000,
                file_type=".raw", category="raw", assay_name="", sample_name=""),
    DataFileRef(relative_path="FILES/DERIVED/b.mzML", size_bytes=2000,
                file_type=".mzml", category="derived", assay_name="",
                sample_name=""),
]


def test_listing_cache_skips_repeat_walks(tmp_path, monkeypatch):
    calls = {"n": 0}

    def patched_walk(url, rel_prefix, files, depth):
        calls["n"] += 1
        files.extend(CANNED)

    monkeypatch.setattr("mtbls_agent.downloader._walk_dir", patched_walk)
    c = StudyCandidate(study_id="MTBLSx")

    d1 = list_data_files(c, cache_dir=str(tmp_path))
    assert calls["n"] == 1                       # walked
    d2 = list_data_files(c, cache_dir=str(tmp_path))
    assert calls["n"] == 1                       # cached — no walk
    assert [dataclasses.asdict(x) for x in d1] == \
        [dataclasses.asdict(x) for x in d2]

    list_data_files(c)                           # no cache dir → walks again
    assert calls["n"] == 2


def test_listing_cache_corrupt_rewalks(tmp_path, monkeypatch):
    calls = {"n": 0}

    def patched_walk(url, rel_prefix, files, depth):
        calls["n"] += 1
        files.extend(CANNED)

    monkeypatch.setattr("mtbls_agent.downloader._walk_dir", patched_walk)
    c = StudyCandidate(study_id="MTBLSx")
    cache = tmp_path / "c" / "file_listings"
    cache.mkdir(parents=True)
    (cache / "MTBLSx.json").write_text("definitely not json")
    out = list_data_files(c, cache_dir=str(tmp_path / "c"))
    assert calls["n"] == 1
    assert out == CANNED                         # repaired + written back
    assert json.loads((cache / "MTBLSx.json").read_text())[0]["relative_path"] \
        == "FILES/RAW/a.raw"


def test_core_download_uses_listing_cache(tmp_path, monkeypatch):
    """The download step wires its cache root into the walk cache: two runs of
    the same download() pipeline → one HTTP listing."""
    import mtbls_agent.downloader as dl
    calls = {"n": 0}

    def patched_walk(url, rel_prefix, files, depth):
        calls["n"] += 1
        files.append(DataFileRef(relative_path="FILES/RAW/x.raw",
                                 size_bytes=10, file_type=".raw",
                                 category="raw"))

    monkeypatch.setattr(dl, "_walk_dir", patched_walk)
    monkeypatch.setattr(dl, "_download_files",
                        lambda files, base, workers, sid: dl.DownloadResult(
                            downloaded=files))
    from mtbls_agent.core.steps import download, pipeline
    c = StudyCandidate(study_id="MTBLSx")
    c.assays = []
    p = pipeline(download(categories=["raw"], dest_dir=str(tmp_path / "d")),
                 input=InspectResult([c])).cache(tmp_path / "root")
    p.run()
    assert calls["n"] == 1
    p.run()                                      # results not cached (off by
    assert calls["n"] == 1                       # default) but listing is
    assert (tmp_path / "root" / "files" / "file_listings" / "MTBLSx.json").exists()