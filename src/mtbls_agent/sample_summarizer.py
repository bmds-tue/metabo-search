"""Per-sample biological sentences for BioBERT embedding.

Every sample from every study gets a **biologically-focused sentence** in a
consistent format so that BioBERT embeddings place similar samples close
together — even across different studies.

Sentence template (deterministic field order):

    [sample: NAME] [study: MTBLS123]
    [organism: Homo sapiens] [tissue: blood plasma] [variant: C57BL/6J]
    [sample_type: biological specimen]
    [Gender: Male] [Age: 45] [Treatment: drug X]
    [condition: Parkinson's disease, biomarker study]
    [abstract: First ~300 characters of the study description from the paper…]

What is included (biological signal only):
- Organism, tissue, variant/strain, sample type
- Study-specific factor values (Gender, Age, Genotype, Treatment, …)
- Design descriptors / disease conditions
- The study abstract / description from the linked paper

What is excluded (no biological signal):
- Technique, instrument, ionization mode, column type
- Protocols, file formats, DOIs (kept in structured fields for traceability)
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from mtbls_agent.models import StudyCandidate

MAX_ABSTRACT_CHARS = 300


# ── Public model ───────────────────────────────────────────────────


@dataclass
class SampleSentence:
    """A biological sentence for one sample, ready for BioBERT embedding."""

    study_id: str
    sample_name: str
    sentence: str
    """Text in the standardised bracket-delimited format — biological fields only."""

    fields: dict[str, str]
    """All structured fields (including technical ones kept for traceability)."""


# ── Public API ─────────────────────────────────────────────────────


def build_sample_sentences(
    candidate: StudyCandidate,
    summarize_fn: Callable[[str], str] | None = None,
    max_abstract_chars: int = MAX_ABSTRACT_CHARS,
) -> list[SampleSentence]:
    """Generate biologically-focused sentences for every sample in a study.

    The abstract/description is **summarized** via ``summarize_fn`` when
    provided (e.g. an LLM call from the agent).  Otherwise it is truncated
    to ``max_abstract_chars`` characters.

    Parameters
    ----------
    candidate : StudyCandidate
        Must have ``inspection_depth == "deep"`` with parsed sample metadata.
    summarize_fn : Callable[[str], str] | None
        Optional function that takes the full abstract text and returns a
        concise summary.  When ``None``, the abstract is truncated.
    max_abstract_chars : int
        Fallback truncation length when no ``summarize_fn`` is given.

    Returns
    -------
    list[SampleSentence]
        One per sample row.  Empty list if not deep-inspected.
    """
    if not candidate.sample_file_parsed or not candidate.sample_metadata:
        return []

    # ── Study-level biological context ──────────────────────────────
    study_id = candidate.study_id
    organism_default = _first_term(candidate.organisms)
    tissue_default = _first_term(candidate.organism_parts)
    condition = ", ".join(d.term for d in candidate.design_descriptors) or "not specified"

    # Abstract: clean HTML → summarise (via LLM) or truncate
    raw_abstract = _clean_html(candidate.description or "")
    if raw_abstract:
        if summarize_fn is not None:
            abstract = summarize_fn(raw_abstract)
        else:
            abstract = _truncate(raw_abstract, max_abstract_chars)
    else:
        abstract = "not specified"

    # ── Column discovery ────────────────────────────────────────────
    fields = candidate.sample_metadata_fields or []
    char_cols = _find_characteristic_columns(fields)
    factor_cols = _find_factor_columns(fields)

    sentences: list[SampleSentence] = []

    for row in candidate.sample_metadata:
        sample_name = row.get("Source Name") or row.get("Sample Name") or "unknown"

        # --- Biological characteristics ---
        organism_val = _get_char(row, char_cols, "Organism") or organism_default
        tissue_val = _get_char(row, char_cols, "Organism part") or tissue_default
        variant_val = _get_char(row, char_cols, "Variant") or "not specified"
        sample_type_val = _get_char(row, char_cols, "Sample type") or "not specified"

        # --- Study-specific factor values (e.g. Gender, Age, Treatment) ---
        factor_parts: list[str] = []
        factor_map: dict[str, str] = {}
        for factor_col_name, full_col in factor_cols:
            val = row.get(full_col, "").strip()
            if val:
                label = factor_col_name.replace("Factor Value[", "").rstrip("]").strip()
                factor_parts.append(f"{label}: {val}")
                factor_map[label] = val

        # --- Build the sentence (biological fields only, fixed order) ---
        parts = [
            f"[sample: {sample_name}]",
            f"[study: {study_id}]",
            f"[organism: {organism_val}]",
            f"[tissue: {tissue_val}]",
            f"[variant: {variant_val}]",
            f"[sample_type: {sample_type_val}]",
        ]
        if factor_parts:
            parts.append(" ".join(f"[{p}]" for p in factor_parts))
        parts.append(f"[condition: {condition}]")
        parts.append(f"[abstract: {abstract}]")

        sentence = " ".join(parts)

        # Structured fields for downstream use
        all_fields = {
            "sample": sample_name,
            "study": study_id,
            "organism": organism_val,
            "tissue": tissue_val,
            "variant": variant_val,
            "sample_type": sample_type_val,
            "condition": condition,
            "abstract": abstract,
            **factor_map,
        }

        sentences.append(
            SampleSentence(
                study_id=study_id,
                sample_name=sample_name,
                sentence=sentence,
                fields=all_fields,
            )
        )

    return sentences


def build_sample_sentences_batch(
    candidates: list[StudyCandidate],
    summarize_fn: Callable[[str], str] | None = None,
    max_workers: int = 10,
    max_abstract_chars: int = MAX_ABSTRACT_CHARS,
) -> list[list[SampleSentence]]:
    """Generate biological sentences for multiple studies in parallel.

    See ``build_sample_sentences`` for parameter documentation.
    """
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {
            ex.submit(build_sample_sentences, c, summarize_fn, max_abstract_chars): i
            for i, c in enumerate(candidates)
        }
        results: list[list[SampleSentence] | None] = [None] * len(candidates)
        for fut in as_completed(futs):
            idx = futs[fut]
            results[idx] = fut.result()
    return [r for r in results if r is not None]


# ── Helpers ────────────────────────────────────────────────────────


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[:max_len].rsplit(" ", 1)[0] + "…"


def _clean_html(text: str) -> str:
    """Strip HTML tags from description text."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _first_term(terms: list[Any]) -> str:
    for t in terms:
        if hasattr(t, "term") and t.term:
            return t.term
        if isinstance(t, dict) and t.get("term"):
            return t["term"]
    return "not specified"


def _find_characteristic_columns(fields: list[str]) -> dict[str, str]:
    """Map ``{short_name: full_column_name}`` for ``Characteristics[...]`` fields."""
    cols: dict[str, str] = {}
    for f in fields:
        if "Characteristics[" in f:
            key = f[f.index("[") + 1 : f.index("]")]
            if key not in cols:
                cols[key] = f
    return cols


def _find_factor_columns(fields: list[str]) -> list[tuple[str, str]]:
    """Return ``[(factor_label, full_column_name)]`` for ``Factor Value[...]`` fields."""
    return [(f, f) for f in fields if "Factor Value[" in f]


def _get_char(row: dict[str, str], char_cols: dict[str, str], key: str) -> str:
    col = char_cols.get(key)
    if col and col in row:
        val = (row.get(col) or "").strip()
        if val:
            return val
    return ""