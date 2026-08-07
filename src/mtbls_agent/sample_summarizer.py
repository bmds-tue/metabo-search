"""Per-sample biological descriptions for BioBERT embedding.

Two-phase design (no LLM callbacks needed):

Phase 1 — Agent calls its LLM once per study using ``build_study_prompt()``::

    prompt = build_study_prompt(candidate)
    study_summary = agent_llm(prompt)  # one LLM call per study

Phase 2 — Library assembles per-sample sentences using the summary::

    sentences = build_sample_sentences(candidate, study_summary)
    for s in sentences:
        print(s.sentence)  # e.g. "Human blood plasma, quality control pooled.
                           # Targeted lipidomics of 433 lipids in human plasma
                           # from 21 healthy subjects."
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from mtbls_agent.models import StudyCandidate


# ── Public model ───────────────────────────────────────────────────


@dataclass
class SampleSentence:
    """A concise biological description of one sample."""

    study_id: str
    sample_name: str
    sentence: str
    """Pure biological description — no names, IDs, software, or techniques."""

    fields: dict[str, str]
    """Raw structured fields for downstream use."""


# ── Phase 1: prompt for the agent's LLM ───────────────────────────


STUDY_SUMMARY_PROMPT = """\
From the study abstract below, write ONE concise sentence describing the
biological context of this study. Focus on: organism, tissue, disease model,
experimental conditions, and biological findings.

STUDY TITLE: {title}

ABSTRACT:
{abstract}

RULES:
- Exclude: sample names, study accession numbers, software names, \
instrument models, analytical techniques, file formats
- Write exactly ONE sentence, concise but informative
- Use standard biomedical terminology"""


def build_study_prompt(candidate: StudyCandidate) -> str:
    """Build the prompt for the agent's LLM to summarise the study biology.

    The agent calls its LLM with this prompt once per study.  The response
    is passed to :func:`build_sample_sentences` as ``study_summary``.
    """
    title = (candidate.title or "")[:200]
    abstract = _clean_html(candidate.description or "")
    return STUDY_SUMMARY_PROMPT.format(title=title, abstract=abstract or "not available")


# ── Phase 2: per-sample assembly ───────────────────────────────────


def build_sample_sentences(
    candidate: StudyCandidate,
    study_summary: str | None = None,
) -> list[SampleSentence]:
    """Assemble per-sample biological sentences from the study summary.

    Parameters
    ----------
    candidate : StudyCandidate
        Deep-inspected study with parsed sample metadata.
    study_summary : str | None
        LLM-generated biological summary of the study (from
        :func:`build_study_prompt`).  When ``None``, a minimal
        facts-only fallback is used instead.

    Returns
    -------
    list[SampleSentence]
        One per sample.  Empty if not deep-inspected.
    """
    if not candidate.sample_file_parsed or not candidate.sample_metadata:
        return []

    # Pre-compute study-level defaults
    organism_default = _first_term(candidate.organisms)
    tissue_default = _first_term(candidate.organism_parts)
    condition = ", ".join(d.term for d in candidate.design_descriptors)

    # Phase 2 may also need a cleaned abstract (fallback)
    raw_abstract = _clean_html(candidate.description or "")

    f = candidate.sample_metadata_fields or []
    char_cols = _find_characteristic_columns(f)
    factor_cols = _find_factor_columns(f)

    sentences: list[SampleSentence] = []

    for row in candidate.sample_metadata:
        sample_name = row.get("Source Name") or row.get("Sample Name") or "unknown"

        organism = _get_char(row, char_cols, "Organism") or organism_default
        tissue = _get_char(row, char_cols, "Organism part") or tissue_default
        variant = _get_char(row, char_cols, "Variant")
        sample_type = _get_char(row, char_cols, "Sample type")

        factor_parts: list[str] = []
        factor_map: dict[str, str] = {}
        for col_name, full_col in factor_cols:
            val = row.get(full_col, "").strip()
            if val:
                label = col_name.replace("Factor Value[", "").rstrip("]").strip()
                factor_parts.append(f"{label}: {val}")
                factor_map[label] = val

        # Build the sentence
        if study_summary:
            # LLM summary + per-sample biological details
            details = [organism, tissue] if organism != tissue else [organism]
            if variant:
                details.append(variant)
            if sample_type:
                details.append(sample_type)
            if factor_parts:
                details.append("(" + "; ".join(factor_parts) + ")")
            detail_str = ", ".join(d for d in details if d)
            sentence = f"{detail_str}. {study_summary}"
        else:
            # Minimal fallback
            parts = [organism]
            if tissue and tissue.lower() != organism.lower():
                parts.append(tissue)
            if variant:
                parts.append(variant)
            if sample_type:
                parts.append(sample_type)
            if factor_parts:
                parts.append("(" + "; ".join(factor_parts) + ")")
            if condition:
                parts.append("[" + condition + "]")
            sentence = ", ".join(parts) + "."

        all_fields = {
            "sample": sample_name,
            "study": candidate.study_id,
            "organism": organism,
            "tissue": tissue,
            "variant": variant or "",
            "sample_type": sample_type or "",
            "condition": condition or "",
            "abstract": raw_abstract[:200] if raw_abstract else "",
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


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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