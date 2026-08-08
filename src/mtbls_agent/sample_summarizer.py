"""Per-sample biological descriptions for BioBERT embedding.

Each sentence contains **only sample-specific information** — no study-level
summary text that would be identical for every sample (that's noise for
embeddings).

Sample-specific fields included:
- Organism, tissue, variant/strain
- Sample type
- Per-sample factor values (disease state, group, treatment, dilution, …)

Study-level context (abstract, design descriptors) is kept in
``SampleSentence.fields`` for downstream use, but is NOT repeated in the
sentence itself.

Example output::

    "Homo sapiens urine, Alzheimer's disease patient, Study reference,
     (Sample Dilution: 100)"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from mtbls_agent.models import StudyCandidate


# ── Public model ───────────────────────────────────────────────────


@dataclass
class SampleSentence:
    """A sample-specific biological description."""

    study_id: str
    sample_name: str
    sentence: str
    """Sample-specific biology — organism, tissue, type, and factors only."""

    fields: dict[str, str]
    """Raw structured fields (incl. study-level context for traceability)."""


# ── Public API ─────────────────────────────────────────────────────


def build_sample_sentences(
    candidate: StudyCandidate,
) -> list[SampleSentence]:
    """Generate a sample-specific sentence for every sample in a study.

    The sentence contains only per-sample biology (organism, tissue, sample
    type, and factor values).  Study-level text is NOT included — it is
    identical for all samples and would pollute the embeddings.

    Parameters
    ----------
    candidate : StudyCandidate
        Deep-inspected study with parsed sample metadata.

    Returns
    -------
    list[SampleSentence]
        One per sample.  Empty if not deep-inspected.
    """
    if not candidate.sample_file_parsed or not candidate.sample_metadata:
        return []

    # Study-level defaults (fallbacks only — not added to the sentence)
    organism_default = _first_term(candidate.organisms)
    tissue_default = _first_term(candidate.organism_parts)

    f = candidate.sample_metadata_fields or []
    char_cols = _find_characteristic_columns(f)
    factor_cols = _find_factor_columns(f)

    sentences: list[SampleSentence] = []

    for row in candidate.sample_metadata:
        sample_name = row.get("Source Name") or row.get("Sample Name") or "unknown"

        # ── Sample-specific biological values ──
        organism = _get_char(row, char_cols, "Organism") or organism_default
        tissue = _get_char(row, char_cols, "Organism part") or tissue_default
        variant = _get_char(row, char_cols, "Variant")
        sample_type = _get_char(row, char_cols, "Sample type")

        # ── Per-sample factor values (disease, group, treatment, …) ──
        factor_parts: list[str] = []
        factor_map: dict[str, str] = {}
        for col_name, full_col in factor_cols:
            val = row.get(full_col, "").strip()
            if val:
                label = col_name.replace("Factor Value[", "").rstrip("]").strip()
                factor_parts.append(f"{label}: {val}")
                factor_map[label] = val

        # ── Build the sentence (sample-specific only) ──
        parts = [organism]
        if tissue and tissue.lower() != organism.lower():
            parts.append(tissue)
        if variant:
            parts.append(variant)
        if sample_type:
            parts.append(sample_type)
        if factor_parts:
            parts.append("(" + "; ".join(factor_parts) + ")")

        sentence = ", ".join(parts) + "."

        # Structured fields for downstream use
        all_fields = {
            "sample": sample_name,
            "study": candidate.study_id,
            "organism": organism,
            "tissue": tissue,
            "variant": variant or "",
            "sample_type": sample_type or "",
            **factor_map,
        }

        sentences.append(
            SampleSentence(
                study_id=candidate.study_id,
                sample_name=sample_name,
                sentence=sentence,
                fields=all_fields,
            )
        )

    return sentences


# ── Helpers ────────────────────────────────────────────────────────


def _first_term(terms: list[Any]) -> str:
    for t in terms:
        if hasattr(t, "term") and t.term:
            return t.term
        if isinstance(t, dict) and t.get("term"):
            return t["term"]
    return ""


def _find_characteristic_columns(fields: list[str]) -> dict[str, str]:
    cols: dict[str, str] = {}
    for f in fields:
        if "Characteristics[" in f:
            key = f[f.index("[") + 1 : f.index("]")]
            if key not in cols:
                cols[key] = f
    return cols


def _find_factor_columns(fields: list[str]) -> list[tuple[str, str]]:
    return [(f, f) for f in fields if "Factor Value[" in f]


def _get_char(row: dict[str, str], char_cols: dict[str, str], key: str) -> str:
    col = char_cols.get(key)
    if col and col in row:
        return (row.get(col) or "").strip()
    return ""