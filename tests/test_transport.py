"""Transport tests: retries, rate limiting, error mapping, typed parsing."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from metabo_search.errors import (
    ClientError,
    NotFound,
    RateLimited,
    ResponseValidationError,
    TransientError,
)
from metabo_search.transport import HttpClient, HttpRecord, RateLimit, RetryPolicy

Handler = Callable[[httpx.Request], httpx.Response]


class _Body(BaseModel):
    value: int


def _client(handler: Handler, **kwargs: object) -> HttpClient:
    """Build a client over a mock transport with instant retries by default."""
    kwargs.setdefault("retry", RetryPolicy(max_attempts=3, base_delay=0.0, jitter=0.0))
    return HttpClient(
        "https://api.test/ws/",
        transport=httpx.MockTransport(handler),
        **kwargs,  # type: ignore[arg-type]
    )


def test_get_json_validates_model() -> None:
    client = _client(lambda request: httpx.Response(200, json={"value": 7}))
    assert client.get_json("thing", model=_Body).value == 7


def test_base_url_and_path_join() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"value": 1})

    _client(handler).get_json("/thing", model=_Body)
    assert seen["url"] == "https://api.test/ws/thing"


def test_retries_transient_status_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"value": 2})

    assert _client(handler).get_json("x", model=_Body).value == 2
    assert calls["n"] == 2


def test_transient_after_all_attempts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(TransientError):
        _client(handler).get_json("x", model=_Body)


def test_rate_limited_carries_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "2"})

    with pytest.raises(RateLimited) as info:
        _client(handler, retry=RetryPolicy(max_attempts=1)).get_json("x", model=_Body)
    assert info.value.retry_after == 2.0


def test_no_retry_on_client_error() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400)

    with pytest.raises(ClientError):
        _client(handler).get_json("x", model=_Body)
    assert calls["n"] == 1


def test_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(NotFound):
        _client(handler).get_json("x", model=_Body)


def test_invalid_body_raises_validation_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": 1})

    with pytest.raises(ResponseValidationError):
        _client(handler).get_json("x", model=_Body)


def test_non_json_body_raises_validation_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(ResponseValidationError):
        _client(handler).get_json("x", model=_Body)


def test_raw_sink_receives_each_attempt() -> None:
    records: list[HttpRecord] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": 1})

    _client(handler, raw_sink=records.append).get_json("x", model=_Body)
    assert len(records) == 1
    assert records[0].status == 200
    assert records[0].attempt == 1
    assert records[0].elapsed_ms >= 0


def test_raw_sink_records_failed_attempts() -> None:
    records: list[HttpRecord] = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"value": 1})

    _client(handler, raw_sink=records.append).get_json("x", model=_Body)
    assert [r.status for r in records] == [503, 200]


def test_post_json_serialises_body() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"value": 3})

    class _Req(BaseModel):
        q: str

    result = _client(handler).post_json("x", model=_Body, body=_Req(q="hello"))
    assert result.value == 3
    assert "hello" in captured["body"]


def test_post_json_requires_one_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": 1})

    with pytest.raises(AssertionError):
        _client(handler).post_json("x", model=_Body)


def test_rate_limit_throttles_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": 1})

    client = _client(handler, rate_limit=RateLimit(requests_per_second=20.0, burst=1))
    started = time.monotonic()
    for _ in range(3):
        client.get_json("x", model=_Body)
    # 3 requests, burst 1, 20/s => ~2 gaps of 50 ms.
    assert time.monotonic() - started >= 0.08


def test_absolute_url_passes_through() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"value": 1})

    _client(handler).get_json("https://other.test/x", model=_Body)
    assert seen["url"] == "https://other.test/x"


def test_download_writes_file(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"hello")

    written = _client(handler).download("file.bin", tmp_path / "out.bin")
    assert written == 5
    assert (tmp_path / "out.bin").read_bytes() == b"hello"
