"""Tests for the Results list subclass."""

from __future__ import annotations

from pydantic import BaseModel

from metabo_search.results import Results


class _Row(BaseModel):
    study_id: str
    title: str


def test_results_behaves_like_a_list() -> None:
    rows: Results[_Row] = Results([_Row(study_id="A", title="a")])
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0].study_id == "A"
    assert [row.title for row in rows] == ["a"]
    rows.append(_Row(study_id="B", title="b"))
    assert rows.ids() == ["A", "B"]


def test_ids_from_dicts() -> None:
    rows: Results[dict[str, str]] = Results([{"study_id": "A"}, {"study_id": "B"}])
    assert rows.ids() == ["A", "B"]


def test_records_dumps_models_and_copies_dicts() -> None:
    rows: Results[object] = Results([_Row(study_id="A", title="a"), {"study_id": "B"}])
    assert rows.records() == [{"study_id": "A", "title": "a"}, {"study_id": "B"}]


def test_fmt_reports_count_and_limit() -> None:
    rows: Results[_Row] = Results([_Row(study_id=str(i), title="t") for i in range(3)])
    text = rows.fmt(limit=2)
    assert "3 result(s)" in text
    assert "1 more" in text
