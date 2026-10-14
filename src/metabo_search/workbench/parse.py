"""Normalization of Metabolomics Workbench REST payloads.

The Workbench returns the same logical table in three shapes: a table keyed by
row index (``{"1": {…}, "2": {…}}``), a flat single record (when exactly one
row matches), or an empty list. :func:`normalize_rows` flattens all three into
a list of row dictionaries.
"""

from __future__ import annotations

from typing import cast

from metabo_search.workbench.models import MetaboliteRow, SampleRow, StudySummary


def normalize_rows(payload: object, id_key: str) -> list[dict[str, object]]:
    """Flatten a Workbench response into a list of row dictionaries.

    Args:
        payload: Parsed JSON (dict / list / anything).
        id_key: A field present in every real row; used to detect the flat
            single-record form.

    Returns:
        The rows; empty for an empty or unexpected payload.
    """
    if isinstance(payload, dict):
        mapping = cast("dict[str, object]", payload)
        if isinstance(mapping.get(id_key), str):
            return [mapping]
        return [
            cast("dict[str, object]", row)
            for row in mapping.values()
            if isinstance(row, dict)
        ]
    if isinstance(payload, list):
        items = cast("list[object]", payload)
        return [cast("dict[str, object]", row) for row in items if isinstance(row, dict)]
    return []


def parse_factor_string(value: str) -> dict[str, str]:
    """Parse the Workbench packed factors string.

    The format is ``"Label:value; Label2:value2 | Label:value"``: groups are
    separated by ``|``, pairs by ``;``, and a pair's label/value by the first
    colon. Pieces without a colon (bare accessions) are ignored.

    Args:
        value: The packed string.

    Returns:
        ``{label: value}``; later duplicates win.
    """
    out: dict[str, str] = {}
    if not value:
        return out
    for group in value.split("|"):
        for piece in group.split(";"):
            label, separator, item = piece.partition(":")
            label, item = label.strip(), item.strip()
            if separator and label and item:
                out[label] = item
    return out


def parse_summaries(payload: object) -> list[StudySummary]:
    """Normalize a ``summary`` payload into study summaries."""
    return [StudySummary.model_validate(row) for row in normalize_rows(payload, "study_id")]


def parse_samples(payload: object) -> list[SampleRow]:
    """Normalize a ``factors`` payload into sample rows with parsed factors."""
    rows = normalize_rows(payload, "study_id")
    samples: list[SampleRow] = []
    for row in rows:
        sample = SampleRow.model_validate(row)
        samples.append(
            sample.model_copy(
                update={"factor_values": parse_factor_string(sample.factors)}
            )
        )
    return samples


def parse_metabolites(payload: object) -> list[MetaboliteRow]:
    """Normalize a ``metabolites`` payload into identified-metabolite rows."""
    return [MetaboliteRow.model_validate(row) for row in normalize_rows(payload, "study_id")]
