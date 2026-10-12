"""Every ingested model field must land in a column or a declared child table.

This is the no-silent-loss guard: adding a field upstream (or to a model) fails
these tests until the field is mapped or declared in the source's ``_EXPANDED``.
"""

from __future__ import annotations

from metabo_search.metabolights import ingest as ml
from metabo_search.workbench import ingest as wb


def _check(source: object) -> None:
    tables = source._TABLES  # type: ignore[attr-defined]
    expanded = source._EXPANDED  # type: ignore[attr-defined]
    columns = {column for cols, _ in tables.values() for column in cols}
    for model, declared in expanded.items():
        fields = set(model.model_fields)
        assert declared <= fields, f"{model.__name__}: stale entries {declared - fields}"
        missing = fields - columns - declared
        assert not missing, f"{model.__name__}: unmapped fields {sorted(missing)}"


def test_metabolights_fields_are_mapped() -> None:
    _check(ml)


def test_workbench_fields_are_mapped() -> None:
    _check(wb)
