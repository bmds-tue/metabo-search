"""Tests for the MetaboLights study-index models (offline fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from metabo_search.metabolights.models import SearchResponse, StudyHit

FIXTURES = Path(__file__).parent / "fixtures" / "metabolights"


def _load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


def _aliases(model: type[BaseModel]) -> set[str]:
    return {field.alias or name for name, field in model.model_fields.items()}


def test_search_fixture_parses() -> None:
    response = SearchResponse.model_validate(_load("search_page.json"))
    assert response.status == "success"
    assert response.content is not None
    hits = response.content.results
    assert hits
    first = hits[0]
    assert first.study_id == "MTBLS1"
    assert first.sample_count == 132
    assert [term.term for term in first.organisms] == ["Homo sapiens"]
    assert first.factor_pairs[0].name == "Gender"
    assert first.assays[0].technique.name == "NMR"


def test_every_mapping_field_is_modelled() -> None:
    mapping = _load("mapping.json")
    properties = set(mapping["content"]["mappings"]["properties"])  # type: ignore[index]
    missing = properties - _aliases(StudyHit)
    assert not missing, f"unmodelled index fields: {sorted(missing)}"


def test_aliases_round_trip_to_wire_format() -> None:
    hit = StudyHit.model_validate({"studyId": "MTBLS1", "sampleCount": 3})
    dumped = hit.model_dump(by_alias=True)
    assert dumped["studyId"] == "MTBLS1"
    assert dumped["sampleCount"] == 3


def test_null_tolerance() -> None:
    hit = StudyHit.model_validate(
        {"studyId": "X", "title": None, "organisms": None, "sampleCount": None}
    )
    assert hit.title == ""
    assert hit.organisms == []
    assert hit.sample_count is None


def test_unknown_fields_are_retained() -> None:
    hit = StudyHit.model_validate({"studyId": "X", "brandNewField": 42})
    assert hit.model_extra is not None
    assert hit.model_extra["brandNewField"] == 42
