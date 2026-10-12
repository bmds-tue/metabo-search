"""MetaboLights client tests (offline, via a mock transport)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from metabo_search.errors import APIError, NotFound
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
