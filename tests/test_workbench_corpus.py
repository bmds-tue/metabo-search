"""Whole-index corpus cache tests (offline, tmp_path + MockTransport)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from metabo_search.errors import MetaboSearchError
from metabo_search.transport import RetryPolicy
from metabo_search.workbench.client import BASE_URL, Workbench
from metabo_search.workbench.corpus import DEFAULT_TTL, CorpusCache

CORPUS = {
    "1": {"study_id": "ST000001", "study_title": "A", "number_of_samples": "24"},
    "2": {"study_id": "ST000002", "study_title": "B", "number_of_samples": "10"},
}
STUDY_IDS = {"ST000001", "ST000002"}


class _Server:
    """A MockTransport handler that counts ``/ST/summary`` requests."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/ST/summary"), f"unexpected {request.url}"
        self.calls += 1
        if self.fail:
            return httpx.Response(500)
        return httpx.Response(200, json=CORPUS)


def _client(
    server: _Server, cache_dir: str | Path | None, *, base_url: str = BASE_URL
) -> Workbench:
    return Workbench(
        base_url=base_url,
        transport=httpx.MockTransport(server),
        retry=RetryPolicy(max_attempts=1),
        cache_dir=cache_dir,
    )


def _seed_cache(
    directory: Path, payload: object, *, age: timedelta, base_url: str = BASE_URL
) -> None:
    """Write a cache entry, then backdate its timestamp by ``age``."""
    cache = CorpusCache(directory, base_url, DEFAULT_TTL)
    cache.write(payload)
    record = json.loads(cache.path.read_text(encoding="utf-8"))
    record["fetched_at"] = (datetime.now(UTC) - age).isoformat()
    cache.path.write_text(json.dumps(record), encoding="utf-8")


def test_miss_fetches_and_writes_the_cache(tmp_path: Path) -> None:
    server = _Server()
    assert _client(server, tmp_path).all_studies().keys() == STUDY_IDS
    assert server.calls == 1
    assert list(tmp_path.glob("wb-corpus-*.json"))


def test_second_client_reads_the_cache(tmp_path: Path) -> None:
    _client(_Server(), tmp_path).all_studies()
    server = _Server()
    assert _client(server, tmp_path).all_studies().keys() == STUDY_IDS
    assert server.calls == 0


def test_cache_dir_none_always_fetches() -> None:
    server = _Server()
    _client(server, None).all_studies()
    _client(server, None).all_studies()
    assert server.calls == 2


def test_fresh_cache_is_not_refetched(tmp_path: Path) -> None:
    _seed_cache(tmp_path, CORPUS, age=timedelta(days=1))
    server = _Server()
    assert _client(server, tmp_path).all_studies().keys() == STUDY_IDS
    assert server.calls == 0


def test_expired_cache_is_refetched(tmp_path: Path) -> None:
    _seed_cache(tmp_path, CORPUS, age=timedelta(days=8))
    server = _Server()
    assert _client(server, tmp_path).all_studies().keys() == STUDY_IDS
    assert server.calls == 1


def test_stale_cache_is_served_when_the_refresh_fails(tmp_path: Path) -> None:
    _seed_cache(tmp_path, CORPUS, age=timedelta(days=8))
    server = _Server(fail=True)
    with pytest.warns(UserWarning, match="stale"):
        studies = _client(server, tmp_path).all_studies()
    assert studies.keys() == STUDY_IDS
    assert server.calls == 1


def test_missing_cache_and_failed_fetch_raises(tmp_path: Path) -> None:
    server = _Server(fail=True)
    with pytest.raises(MetaboSearchError):
        _client(server, tmp_path).all_studies()


def test_refresh_corpus_always_fetches_and_rewrites(tmp_path: Path) -> None:
    server = _Server()
    wb = _client(server, tmp_path)
    wb.all_studies()
    assert server.calls == 1
    assert wb.refresh_corpus().keys() == STUDY_IDS
    assert server.calls == 2
    third = _Server()
    assert _client(third, tmp_path).all_studies().keys() == STUDY_IDS
    assert third.calls == 0


def test_cache_is_per_base_url(tmp_path: Path) -> None:
    first = _Server()
    _client(first, tmp_path, base_url="https://a.example/rest").all_studies()
    second = _Server()
    _client(second, tmp_path, base_url="https://b.example/rest").all_studies()
    assert first.calls == 1
    assert second.calls == 1


def test_corrupt_cache_is_ignored(tmp_path: Path) -> None:
    cache = CorpusCache(tmp_path, BASE_URL, DEFAULT_TTL)
    cache.path.parent.mkdir(parents=True, exist_ok=True)
    cache.path.write_text("not json", encoding="utf-8")
    server = _Server()
    assert _client(server, tmp_path).all_studies().keys() == STUDY_IDS
    assert server.calls == 1
