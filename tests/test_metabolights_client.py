"""MetaboLights client tests (offline, via a mock transport)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from metabo_search.errors import APIError, MetaboSearchError, NotFound
from metabo_search.metabolights.client import MetaboLights
from metabo_search.transport import RetryPolicy

FIXTURES = Path(__file__).parent / "fixtures" / "metabolights"
Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> MetaboLights:
    return MetaboLights(
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=1),
    )


def _envelope(results: list[dict[str, object]], total: int) -> dict[str, object]:
    return {
        "status": "success",
        "successMessage": None,
        "errorMessage": None,
        "errors": [],
        "content": {
            "results": results,
            "totalResults": total,
            "requestId": "test",
            "facets": {},
        },
    }


def _fixture_hits() -> list[dict[str, object]]:
    page = json.loads((FIXTURES / "search_page.json").read_text())
    return page["content"]["results"]


def test_search_returns_hits() -> None:
    hits = _fixture_hits()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(hits, len(hits)))

    result = _client(handler).search("anything")
    assert result.ids() == [str(hit["studyId"]) for hit in hits]


def test_search_sends_facet_filters() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json=_envelope([], 0))

    _client(handler).search(
        "q",
        organisms=["Homo sapiens"],
        sample_types=["blood plasma"],
        techniques=["LC-MS"],
        design_descriptors=["lipidomics"],
    )
    body = captured["body"]
    assert isinstance(body, dict)
    fields = {f["field"]: f["values"] for f in body["filters"]}
    assert fields["organisms.term"] == ["Homo sapiens"]
    assert fields["organismParts.term"] == ["blood plasma"]
    assert fields["assayTechniques.name"] == ["LC-MS"]
    assert fields["designDescriptors.term"] == ["lipidomics"]


def test_search_applies_min_samples() -> None:
    hits = _fixture_hits()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(hits, len(hits)))

    assert _client(handler).search(min_samples=100_000) == []


def test_search_paginates_until_limit() -> None:
    page1 = [{"studyId": f"MTBLS{i}"} for i in range(1, 101)]
    page2 = [{"studyId": f"MTBLS{i}"} for i in range(101, 201)]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        hits = page1 if body["page"]["current"] == 1 else page2
        return httpx.Response(200, json=_envelope(hits, 200))

    assert len(_client(handler).search(limit=150)) == 150


def test_study_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope([], 0))

    with pytest.raises(NotFound):
        _client(handler).study("MTBLS404")


def test_api_error_is_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "error",
                "successMessage": None,
                "errorMessage": "boom",
                "errors": [],
                "content": None,
            },
        )

    with pytest.raises(APIError) as info:
        _client(handler).search("q")
    assert "boom" in str(info.value)


def test_studies_preserves_input_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        ids = body["filters"][0]["values"]
        hits = [{"studyId": sid} for sid in reversed(ids)]
        return httpx.Response(200, json=_envelope(hits, len(hits)))

    result = _client(handler).studies(["MTBLS2", "MTBLS1"])
    assert result.ids() == ["MTBLS2", "MTBLS1"]


ISA_SAMPLES = (
    "Sample Name\tSource Name\tCharacteristics[Organism]\t"
    "Characteristics[Organism part]\n"
    "DCR00004_U\tHuman1\tHomo sapiens\turine\n"
)
ISA_ASSAY = (
    "Sample Name\tRaw Spectral Data File\n"
    "DCR00004_U\tFILES/RAW_FILES/DCR00004_U.raw\n"
)


def test_inspect_downloads_and_parses_isa() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/MTBLS1/"):
            return httpx.Response(
                200,
                text='<a href="s_MTBLS1.txt">s</a><a href="a_MTBLS1.txt">a</a>',
            )
        if url.endswith("s_MTBLS1.txt"):
            return httpx.Response(200, text=ISA_SAMPLES)
        if url.endswith("a_MTBLS1.txt"):
            return httpx.Response(200, text=ISA_ASSAY)
        return httpx.Response(404)

    details = _client(handler).inspect(["MTBLS1"])
    assert details[0].study_id == "MTBLS1"
    assert details[0].samples[0].raw_files == ["FILES/RAW_FILES/DCR00004_U.raw"]


def test_inspect_collects_per_study_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/MTBLS1/"):
            return httpx.Response(200, text='<a href="s_MTBLS1.txt">s</a>')
        if url.endswith("s_MTBLS1.txt"):
            return httpx.Response(200, text=ISA_SAMPLES)
        return httpx.Response(404)  # MTBLS2 listing fails

    details = _client(handler).inspect(["MTBLS1", "MTBLS2"])
    assert details[0].error == ""
    assert details[0].samples
    assert details[1].study_id == "MTBLS2"
    assert details[1].error != ""
    assert details[1].samples == []


def test_inspect_raises_when_asked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(MetaboSearchError):
        _client(handler).inspect(["MTBLS1"], on_error="raise")


def test_inspect_flags_missing_isa_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/MTBLS14005/"):
            return httpx.Response(200, text='<a href="FILES/">FILES/</a>')
        return httpx.Response(404)

    details = _client(handler).inspect(["MTBLS14005"])
    assert details[0].samples == []
    assert "ISA" in details[0].error


def test_search_defaults_to_stable_sort() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json=_envelope([], 0))

    _client(handler).search("q")
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["sort"] == {"field": "studyId", "direction": "asc"}
