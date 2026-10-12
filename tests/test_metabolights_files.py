"""Tests for the FILES/ walker and selective download (offline)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from metabo_search.metabolights.files import (
    categorize,
    detect_extension,
    download,
    list_files,
    parse_size,
)
from metabo_search.transport import HttpClient, RetryPolicy

Handler = Callable[[httpx.Request], httpx.Response]

ROOT_HTML = """
<table>
<tr><td><a href="RAW_FILES/">RAW_FILES/</a></td><td>2020-01-01</td><td>-</td></tr>
<tr><td><a href="DERIVED_FILES/">DERIVED_FILES/</a></td><td>2020-01-01</td><td>-</td></tr>
<tr><td><a href="readme.txt">readme.txt</a></td><td>2020-01-01</td><td>1.0K</td></tr>
<tr><td><a href="/">Parent Directory</a></td><td></td><td>-</td></tr>
</table>
"""
RAW_HTML = """
<table>
<tr><td><a href="sample1.raw">sample1.raw</a></td><td>2020-01-01</td><td>2.1M</td></tr>
<tr><td><a href="sample1.mzML">sample1.mzML</a></td><td>2020-01-01</td><td>3M</td></tr>
</table>
"""
DERIVED_HTML = """
<table>
<tr><td><a href="sample1.mzML">sample1.mzML</a></td><td>2020-01-01</td><td>512K</td></tr>
</table>
"""


def _http(handler: Handler) -> HttpClient:
    return HttpClient(
        "https://api.test/",
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=1),
    )


def _listing_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/FILES/"):
        return httpx.Response(200, text=ROOT_HTML)
    if url.endswith("/FILES/RAW_FILES/"):
        return httpx.Response(200, text=RAW_HTML)
    if url.endswith("/FILES/DERIVED_FILES/"):
        return httpx.Response(200, text=DERIVED_HTML)
    if url.endswith("sample1.raw"):
        return httpx.Response(200, content=b"RAWBYTES")
    if url.endswith("sample1.mzML"):
        return httpx.Response(200, content=b"MZMLBYTES")
    return httpx.Response(404)


def test_parse_size() -> None:
    assert parse_size("2.1M") == int(2.1 * 1024**2)
    assert parse_size("512K") == 512 * 1024
    assert parse_size("") == 0
    assert parse_size("junk") == 0


def test_detect_extension_keeps_compound_suffix() -> None:
    assert detect_extension("x.d.zip") == ".d.zip"
    assert detect_extension("x.mzML") == ".mzml"
    assert detect_extension("x") == ""


def test_categorize_directory_beats_extension() -> None:
    assert categorize(".mzml", "FILES/RAW_FILES/x.mzML") == "raw"
    assert categorize(".mzml", "FILES/DERIVED_FILES/x.mzML") == "derived"
    assert categorize(".mzml", "FILES/x.mzML") == "derived"
    assert categorize(".raw", "FILES/x.raw") == "raw"
    assert categorize(".txt", "FILES/x.txt") == "other"


def test_list_files_walks_and_categorizes() -> None:
    files = list_files(_http(_listing_handler), "MTBLS1")
    by_path = {file.path: file for file in files}
    assert by_path["FILES/RAW_FILES/sample1.raw"].category == "raw"
    assert by_path["FILES/RAW_FILES/sample1.raw"].size_bytes == int(2.1 * 1024**2)
    assert by_path["FILES/DERIVED_FILES/sample1.mzML"].category == "derived"
    assert by_path["FILES/readme.txt"].category == "other"


def test_list_files_non_recursive() -> None:
    files = list_files(_http(_listing_handler), "MTBLS1", recursive=False)
    assert [file.name for file in files] == ["readme.txt"]


def test_download_writes_selected_files(tmp_path: Path) -> None:
    report = download(_http(_listing_handler), "MTBLS1", "raw", dest=tmp_path)
    assert report.failed == []
    assert [file.name for file in report.downloaded] == ["sample1.raw", "sample1.mzML"]
    assert report.total_bytes == len(b"RAWBYTES") + len(b"MZMLBYTES")
    assert (tmp_path / "MTBLS1" / "FILES/RAW_FILES/sample1.raw").read_bytes() == b"RAWBYTES"


def test_download_reports_failures(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/FILES/"):
            return httpx.Response(200, text=ROOT_HTML)
        if url.endswith("/FILES/RAW_FILES/"):
            return httpx.Response(200, text=RAW_HTML)
        if url.endswith("/FILES/DERIVED_FILES/"):
            return httpx.Response(200, text=DERIVED_HTML)
        return httpx.Response(404)  # file downloads fail

    report = download(_http(handler), "MTBLS1", "raw", dest=tmp_path)
    assert report.failed == ["FILES/RAW_FILES/sample1.raw", "FILES/RAW_FILES/sample1.mzML"]
