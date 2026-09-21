"""Regression tests for the parallel-agent bug report (server/API hardening).

- Bug 1: /metabolites payload has 3 shapes (table dict, EMPTY LIST for the
  largest studies, transient disconnects) — _metabolite_count must tolerate
  them; a list payload previously raised AttributeError OUTSIDE the soft-fail
  try, aborting the whole workbench deep pass.  The "count unknown, not 0"
  distinction now lands in metabolite_list_unavailable and the scorer.
- Bug 2: client.get() had no retry on transient transport failures (metstat
  and the corpus did, the generic endpoint did not).
- Bug 3: connection-pool cap (throttle instead of server overload => shallow
  candidates); REST fallback 400s by design (never terminal) + final HTTP
  retry; the pipeline refuses to cache a degraded (shallow-leftover) inspect.
"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path

import httpx

from metabo_search import InspectResult
from metabo_search.core.steps import _inspect_cache_healthy
from metabo_search.models import StudyCandidate
from metabo_search.repositories.workbench import inspect as wb_inspect
from metabo_search.repositories.workbench import client as wb_client

from conftest import FIXTURES_DIR


# ──────────────────────────────────────────────────────────────
# Bug 1 — /metabolites shape tolerance
# ──────────────────────────────────────────────────────────────


def test_metabolite_count_table_dict():
    payload = {"1": {"metabolite_name": "A"}, "2": {"metabolite_name": "B"},
               "3": {"metabolite_name": ""}}        # empty name row ignored
    n, unavailable = wb_inspect._metabolite_count(payload)
    assert n == 2 and unavailable is False


def test_metabolite_count_empty_list_is_unavailable_not_zero():
    # ST004975/ST001408/…: the API returns [] and OMITS the list for the
    # largest (richest) studies.  Previous code: AttributeError → whole deep
    # pass aborted.  Now: count unknown, flagged, never 0.
    n, unavailable = wb_inspect._metabolite_count([])
    assert n is None and unavailable is True


def test_metabolite_count_list_with_rows():
    payload = [{"metabolite_name": "A"}, {"metabolite_name": "B"}]
    n, unavailable = wb_inspect._metabolite_count(payload)
    assert n == 2 and unavailable is False


def test_metabolite_count_other_shapes():
    assert wb_inspect._metabolite_count(None) == (None, True)
    assert wb_inspect._metabolite_count("junk") == (None, True)
    # flat single-record form: dict whose values are strings — no crash
    flat = {"study_id": "ST1", "metabolite_name": "A"}
    assert wb_inspect._metabolite_count(flat) == (None, False)


def test_deep_metadata_list_metabolites_does_not_abort_the_pass():
    """The reported crash: one list-shaped study killed every candidate."""
    def loader(study_id: str, kind: str):
        d = json.loads((FIXTURES_DIR / f"study_ST000001_{kind}.json").read_text())
        if kind == "metabolites":
            return []                       # large-study shape
        return d

    ok = StudyCandidate(study_id="ST000001", repository="metabolomics_workbench")
    deep = wb_inspect.deep_metadata([ok], workers=1, load_payload=loader)[0]
    # pass completes; candidate deep (factors/analysis merged), count unknown
    assert deep.inspection_depth == "deep"
    assert deep.metabolite_count is None
    assert deep.metabolite_list_unavailable is True
    assert deep.sample_count == 24


def test_scorer_unknown_list_is_not_a_hard_fail():
    from metabo_search.scorer import _score_one
    from metabo_search.models import OntologyTerm, RequirementProfile, \
        StudyRequirements

    c = StudyCandidate(study_id="ST004975", repository="metabolomics_workbench",
                       organisms=[OntologyTerm(term="Homo sapiens")],
                       sample_metadata=[{"sample_source": "Blood"}],
                       sample_count=1, sample_file_parsed=True,
                       metabolite_count=None, metabolite_list_unavailable=True)
    profile = RequirementProfile(
        hard=StudyRequirements(min_metabolites=100))
    sc = _score_one(c, profile)
    assert sc.hard_passed            # unknown count must NOT hard-fail
    assert not any("min metabolites" in r for r in sc.hard_fail_reasons)
    assert "UNAVAILABLE" in sc.criterion_explanations["min_metabolites (hard)"]


# ──────────────────────────────────────────────────────────────
# Bug 2 — client.get() retries transport failures
# ──────────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content or (io.BytesIO(content).getvalue())
        self.text = "" if payload is None else json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://example.test")
            raise httpx.HTTPStatusError(
                f"{self.status_code} error", request=req,
                response=httpx.Response(self.status_code, request=req))

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError("no json", "", 0)
        return self._payload


class _FakeClient:
    def __init__(self, script):
        self.script = list(script)      # responses or Exception instances
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step


def _patch_client(monkeypatch, fake):
    monkeypatch.setattr(wb_client, "_http_client", lambda: fake)
    sleeps: list[float] = []
    monkeypatch.setattr(wb_client, "time", type("T", (), {"sleep": sleeps.append}))
    return sleeps


def test_get_retries_transport_failure_then_succeeds(monkeypatch):
    fake = _FakeClient([httpx.RemoteProtocolError("disconnect"),
                        httpx.RemoteProtocolError("disconnect"),
                        _FakeResp(200, payload={"ok": True})])
    _patch_client(monkeypatch, fake)
    assert wb_client.get("/study/study_id/ST1/summary") == {"ok": True}
    assert fake.calls == 3


def test_get_does_not_retry_4xx(monkeypatch):
    fake = _FakeClient([_FakeResp(400), _FakeResp(200, payload="never")])
    _patch_client(monkeypatch, fake)
    try:
        wb_client.get("/study/study_id/ST1/summary")
        raise AssertionError("400 should raise")
    except httpx.HTTPStatusError:
        assert fake.calls == 1       # terminal — no pointless backoff sleeps


def test_get_retries_5xx(monkeypatch):
    fake = _FakeClient([_FakeResp(503), _FakeResp(200, payload={"slow": True})])
    sleeps = _patch_client(monkeypatch, fake)
    assert wb_client.get("/study/study_id/ST1/summary") == {"slow": True}
    assert fake.calls == 2
    assert sleeps == [2.0]           # 2*(attempt+1) backoff


# ──────────────────────────────────────────────────────────────
# Bug 3.1 — connection-pool cap (throttle, not overload)
# ──────────────────────────────────────────────────────────────


def test_http_clients_bounded_connection_pool():
    from metabo_search import downloader, inspector

    for mod in (inspector, downloader):
        client = mod._http_client()
        pool = client._transport._pool
        assert pool._max_connections == 16, \
            f"{mod.__name__} client must cap the pool (max_connections=16)"
        assert pool._max_keepalive_connections == 16


# ──────────────────────────────────────────────────────────────
# Bug 3.2 — REST fallback is never terminal
# ──────────────────────────────────────────────────────────────


def test_download_via_rest_returns_false_on_400():
    from metabo_search import inspector

    fake = _FakeClient([_FakeResp(400)])
    original = inspector._http_client
    inspector._http_client = lambda: fake
    try:
        assert inspector._download_via_rest(
            "MTBLS7776", Path(tempfile.mkdtemp())) is False
    finally:
        inspector._http_client = original


def test_download_via_rest_returns_true_on_zip():
    from metabo_search import inspector

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("i_MTBLS1.txt", "hello")
    fake = _FakeClient([_FakeResp(200, content=buf.getvalue())])
    original = inspector._http_client
    inspector._http_client = lambda: fake
    try:
        assert inspector._download_via_rest("MTBLS1", Path(tempfile.mkdtemp())) is True
    finally:
        inspector._http_client = original


# ──────────────────────────────────────────────────────────────
# Bug 3.3 — the pipeline refuses to cache a degraded inspect
# ──────────────────────────────────────────────────────────────


def test_inspect_cache_health_detects_shallow_leftovers():
    deep_c = StudyCandidate(study_id="ST1", investigation_file_parsed=True,
                            sample_file_parsed=True)
    shallow_c = StudyCandidate(study_id="ST2")        # failed download
    assert _inspect_cache_healthy(InspectResult(
        candidates=[deep_c], isa_dirs={})) is True
    assert _inspect_cache_healthy(InspectResult(
        candidates=[deep_c, shallow_c], isa_dirs={})) is False


def test_empty_inspect_result_is_cacheable():
    assert _inspect_cache_healthy(InspectResult(
        candidates=[], isa_dirs={})) is True