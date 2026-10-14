"""Every ingested model field must reach a column or a child row.

A sentinel test: build each ingestible model with every field set to a unique
marker, map it, and assert every marker lands in a produced row. Adding a model
field without mapping it fails the test instead of being silently dropped.
"""

from __future__ import annotations

from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from metabo_search.metabolights import ingest as ml
from metabo_search.metabolights.models import StudyDetail as MLDetail
from metabo_search.metabolights.models import StudyHit
from metabo_search.workbench import ingest as wb
from metabo_search.workbench.models import StudyDetail as WBDetail
from metabo_search.workbench.models import StudySummary

_DROPPED: dict[type[BaseModel], frozenset[str]] = {
    MLDetail: frozenset({"error"}),
    # Workbench children carry their own study_id, so the detail's is unused.
    WBDetail: frozenset({"error", "study_id"}),
}
"""Fields that are deliberately not persisted."""


def _marker(path: str, markers: list[str]) -> str:
    """Record and return a unique marker for one field path."""
    value = f"MK::{path}"
    markers.append(value)
    return value


def _value(annotation: Any, path: str, markers: list[str]) -> Any:
    """Build a marker (or a nested marker container) for one field annotation."""
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation) or (str,)
        return [_value(args[0], f"{path}[]", markers)]
    if origin is dict:
        args = get_args(annotation) or (str, str)
        return {
            _value(args[0], f"{path}.key", markers): _value(
                args[1], f"{path}.value", markers
            )
        }
    if origin in (Union, UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        assert len(args) == 1, f"{path}: unsupported union {annotation}"
        return _value(args[0], path, markers)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _populate(annotation, path, markers)
    return _marker(path, markers)


def _populate(model: type[BaseModel], prefix: str, markers: list[str]) -> BaseModel:
    """Construct ``model`` with every persisted field set to a marker.

    ``model_construct`` bypasses validation on purpose: markers are strings even
    for numeric fields, and only :func:`batch` (not ``write``) sees them.
    """
    dropped = _DROPPED.get(model, frozenset())
    values = {
        name: _value(field.annotation, f"{prefix}.{name}", markers)
        for name, field in model.model_fields.items()
        if name not in dropped
    }
    return model.model_construct(**values)


def _assert_mapped(source: Any, obj: object, markers: list[str]) -> None:
    """Assert every marker appears as a value in the batch's rows."""
    seen = {
        value
        for table in source.batch(obj).tables
        for row in table.rows
        for value in row.values()
    }
    missing = [marker for marker in markers if marker not in seen]
    assert not missing, f"{source.__name__}: unmapped fields {missing}"


def test_metabolights_fields_are_mapped() -> None:
    markers: list[str] = []
    hit = _populate(StudyHit, "StudyHit", markers)
    detail = _populate(MLDetail, "StudyDetail", markers)
    _assert_mapped(ml, [hit, detail], markers)


def test_workbench_fields_are_mapped() -> None:
    markers: list[str] = []
    summary = _populate(StudySummary, "StudySummary", markers)
    detail = _populate(WBDetail, "StudyDetail", markers)
    _assert_mapped(wb, [summary, detail], markers)
