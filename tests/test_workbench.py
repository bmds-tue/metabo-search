"""Metabolomics Workbench client and parsing tests (offline fixtures)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from metabo_search.errors import MetaboSearchError, NotFound, ResponseValidationError
from metabo_search.transport import RetryPolicy
from metabo_search.workbench.client import Workbench
from metabo_search.workbench.models import StudySummary
from metabo_search.workbench.parse import normalize_rows, parse_factor_string

FIXTURES = Path(__file__).parent / "fixtures" / "workbench"
Handler = Callable[[httpx.Request], httpx.Response]

CORPUS = {
    "1": {
        "study_id": "ST000001",
        "study_title": "Fatb Induction Experiment",
        "number_of_samples": "24",
    },
    "2": {
        "study_id": "ST000002",
        "study_title": "Diabetes urine metabolomics",
        "number_of_samples": "10",
    },
}


def _fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/ST/summary"):
        return httpx.Response(200, json=CORPUS)
    if url.endswith("/ST000001/summary"):
        return httpx.Response(200, json=_fixture("study_ST000001_summary.json"))
    if url.endswith("/ST000001/factors"):
        return httpx.Response(200, json=_fixture("study_ST000001_factors.json"))
    if url.endswith("/ST000001/analysis"):
        return httpx.Response(200, json=_fixture("study_ST000001_analysis.json"))
    if url.endswith("/ST000001/metabolites"):
        return httpx.Response(200, json=_fixture("study_ST000001_metabolites.json"))
    return httpx.Response(404)


def _client() -> Workbench:
    return Workbench(
        transport=httpx.MockTransport(_handler),
        retry=RetryPolicy(max_attempts=1),
        cache_dir=None,
    )


def test_normalize_rows_shapes() -> None:
    assert normalize_rows({"1": {"study_id": "A"}, "2": {"study_id": "B"}}, "study_id") == [
        {"study_id": "A"},
        {"study_id": "B"},
    ]
    assert normalize_rows({"study_id": "A"}, "study_id") == [{"study_id": "A"}]
    assert normalize_rows([], "study_id") == []
    assert normalize_rows({}, "study_id") == []


def test_parse_factor_string() -> None:
    packed = "Arabidopsis Genotype:fatb-ko KD; At1g08510 | Wounding:Control - Non-Wounded"
    assert parse_factor_string(packed) == {
        "Arabidopsis Genotype": "fatb-ko KD",
        "Wounding": "Control - Non-Wounded",
    }
    assert parse_factor_string("") == {}


def test_study_summary() -> None:
    study = _client().study("ST000001")
    assert study.study_id == "ST000001"
    assert study.analysis_type == "GC-MS"
    assert study.number_of_samples == 24


def test_samples_parse_factors() -> None:
    samples = _client().samples("ST000001")
    assert samples[0].local_sample_id == "LabF_115904"
    assert samples[0].sample_source == "Plant"
    assert samples[0].factor_values["Arabidopsis Genotype"] == "fatb-ko KD"


def test_analyses() -> None:
    analyses = _client().analyses("ST000001")
    assert analyses[0].analysis_id == "AN000001"
    assert analyses[0].ms_instrument_name == "Leco Pegasus III GC TOF"


def test_metabolites() -> None:
    metabolites = _client().metabolites("ST000001")
    assert metabolites[0].metabolite_name == "1,2,4-benzenetriol"
    assert metabolites[0].refmet_name == "1,2,4-Trihydroxybenzene"


def test_search_ranks_by_title_overlap() -> None:
    results = _client().search("diabetes")
    assert results.ids() == ["ST000002"]


def test_search_min_samples() -> None:
    results = _client().search("", min_samples=20)
    assert results.ids() == ["ST000001"]


def test_study_not_found() -> None:
    with pytest.raises(NotFound):
        _client().study("ST999999")


def test_inspect_bundles_detail() -> None:
    detail = _client().inspect(["ST000001"])[0]
    assert detail.study_id == "ST000001"
    assert detail.summary is not None
    assert detail.samples
    assert detail.analyses
    assert detail.metabolites


def test_inspect_collects_per_study_errors() -> None:
    details = _client().inspect(["ST000001", "ST999999"])
    assert details[0].error == ""
    assert details[0].samples
    assert details[1].study_id == "ST999999"
    assert details[1].error != ""
    assert details[1].samples == []


def test_inspect_raises_when_asked() -> None:
    with pytest.raises(MetaboSearchError):
        _client().inspect(["ST999999"], on_error="raise")


def _counting_client(paths: list[str]) -> Workbench:
    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(str(request.url))
        return _handler(request)

    return Workbench(
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=1),
        cache_dir=None,
    )


def test_inspect_reuses_a_passed_summary_without_refetching() -> None:
    paths: list[str] = []
    wb = _counting_client(paths)
    summary = StudySummary(study_id="ST000001", study_title="reused")
    detail = wb.inspect([summary], include_analyses=False, include_metabolites=False)[0]
    assert detail.summary is summary
    assert detail.samples
    assert len(paths) == 1
    assert paths[0].endswith("/ST000001/factors")


def test_inspect_samples_only_makes_one_request_per_study() -> None:
    paths: list[str] = []
    wb = _counting_client(paths)
    details = wb.inspect(
        [StudySummary(study_id="ST000001")],
        include_analyses=False,
        include_metabolites=False,
    )
    assert len(paths) == 1
    assert paths[0].endswith("/ST000001/factors")
    assert details[0].analyses == []
    assert details[0].metabolites == []


def test_inspect_accessions_can_skip_the_summary() -> None:
    paths: list[str] = []
    wb = _counting_client(paths)
    detail = wb.inspect(
        ["ST000001"],
        include_summary=False,
        include_analyses=False,
        include_metabolites=False,
    )[0]
    assert detail.summary is None
    assert detail.samples
    assert len(paths) == 1
    assert paths[0].endswith("/ST000001/factors")


def test_inspect_can_skip_every_part() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request {request.url}")

    wb = Workbench(
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=1),
        cache_dir=None,
    )
    detail = wb.inspect(
        [StudySummary(study_id="ST000001")],
        include_summary=False,
        include_samples=False,
        include_analyses=False,
        include_metabolites=False,
    )[0]
    assert detail.summary is None
    assert detail.samples == []
    assert detail.analyses == []
    assert detail.metabolites == []


def test_non_json_body_raises_response_validation_error() -> None:
    wb = Workbench(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>oops</html>")),
        retry=RetryPolicy(max_attempts=1),
        cache_dir=None,
    )
    with pytest.raises(ResponseValidationError):
        wb.study("ST000001")
