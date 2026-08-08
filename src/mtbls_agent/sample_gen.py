"""Study-profile driven per-sample biological sentence generation.

Option-D design: **zero per-sample LLM calls.**  The agent's LLM is invoked
once per study to *thoroughly study the metadata structure*, decode codes
(disease/group), and author a **sentence recipe** with named slots and
explicit slot->source mappings.  The library then applies the recipe
deterministically to every sample.

LLM calls per study: ~1 (the profile).  Per-sample work: deterministic.

The library only *prepares* the profiling prompt and *applies* the returned
recipe — it never calls the LLM itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mtbls_agent.models import StudyCandidate


# ── Public models ──────────────────────────────────────────────────


@dataclass
class SampleContext:
    """Full raw context for one sample — everything a recipe needs."""

    study_id: str
    sample_name: str
    organism: str = ""
    tissue: str = ""
    variant: str = ""
    sample_type: str = ""
    factors: dict[str, str] = field(default_factory=dict)
    characteristics: dict[str, str] = field(default_factory=dict)
    raw_data_files: list[str] = field(default_factory=list)
    derived_data_files: list[str] = field(default_factory=list)
    assay_techniques: list[str] = field(default_factory=list)


@dataclass
class SampleDescription:
    """A generated human-readable sentence for one sample."""

    study_id: str
    sample_name: str
    sentence: str
    """Human-readable biological sentence (for BioBERT embedding)."""
    used_sources: list[str] = field(default_factory=list)
    """Which slot sources / codes were used."""


@dataclass
class StudyProfile:
    """The LLM-authored recipe for describing this study's samples."""

    codes: dict[str, str] = field(default_factory=dict)
    """Decoded abbreviations (e.g. ALZ -> Alzheimer's disease)."""
    sentence_template: str = ""
    """Template with named slots, e.g. 'Homo sapiens {tissue}, {disease} \\
    patient, {Sex}, age {Age}, (Sample Dilution: {Dilution}).'"""
    slot_sources: dict[str, dict[str, str]] = field(default_factory=dict)
    """slot -> {\"type\": \"organism|tissue|variant|sample_type|factor|\\
    characteristic|code|qc\", \"field\": \"...\"}"""
    qc_string: str = "quality control sample"
    """Sentence fragment for QC/reference/dilution samples."""
    study_context: str = ""
    """Short phrase; kept for traceability, not repeated per sample."""


# ── Context collection ─────────────────────────────────────────────


def collect_sample_contexts(
    candidate: StudyCandidate,
) -> list[SampleContext]:
    """Collect the full raw context for every sample in a study.

    Includes factors, characteristics, and crucially the **associated data
    file names** (from ``sample_file_map``), which often carry the disease
    code (e.g. ``ALZ_RNEG_ToF03_U4W15.mzML``).
    """
    if not candidate.sample_file_parsed or not candidate.sample_metadata:
        return []

    organism_default = _first_term(candidate.organisms)
    tissue_default = _first_term(candidate.organism_parts)
    techniques = sorted({a.technique_name for a in candidate.assays if a.technique_name})
    file_map = candidate.sample_file_map or {}

    contexts = []
    for row in candidate.sample_metadata:
        src_name = row.get("Source Name") or ""
        samp_name = row.get("Sample Name") or ""
        sample_name = samp_name or src_name or "unknown"

        organism = _get_char(row, "Organism") or organism_default
        tissue = _get_char(row, "Organism part") or tissue_default

        factors = _extract_bracketed(row, "Factor Value[")
        characteristics = _extract_bracketed(row, "Characteristics[")

        mapped = file_map.get(sample_name) or file_map.get(src_name, {})

        contexts.append(SampleContext(
            study_id=candidate.study_id,
            sample_name=sample_name,
            organism=organism,
            tissue=tissue,
            variant=_get_char(row, "Variant"),
            sample_type=_get_char(row, "Sample type"),
            factors=factors,
            characteristics=characteristics,
            raw_data_files=list(mapped.get("raw", [])),
            derived_data_files=list(mapped.get("derived", [])),
            assay_techniques=techniques,
        ))

    return contexts


# ── Study-profile prompt (ONE LLM call per study) ──────────────────


STUDY_PROFILE_PROMPT = """\
You are designing how to describe the samples of a metabolomics study with \
single, human-readable, biologically informative sentences used as metadata \
embeddings. Before writing anything, THOROUGHLY study where every piece of \
information lives in this study.

STEP 1 — STUDY THE METADATA LAYOUT
The ISA sample-file columns are:
__COLUMNS__

Example sample rows (Source Name = sample file key, Sample Name = assay key):
__EXAMPLE_ROWS__

STEP 2 — STUDY THE DATA-FILE CODES (disease/group often hide here)
Distinct data-file name stems:
__FILE_STEMS__

STEP 3 — STUDY THE FACTORS
Factor names and their unique values:
__FACTOR_SUMMARY__

STEP 4 — STUDY THE STUDY
TITLE: __TITLE__
ABSTRACT: __ABSTRACT__

Now produce the description recipe. Return STRICT JSON with these keys:
1. "codes": object mapping each abbreviation/code (from sample names, data \
file names, factor values) to its biological meaning. Infer cryptic ones from \
the abstract (e.g. ALZ -> "Alzheimer's disease", CTL -> "cognitively normal \
control", MCI -> "mild cognitive impairment").
2. "sentence_template": a template string with named {slots} that, when \
filled for one sample, reads as ONE natural biological sentence. Use a \
{disease} slot for the decoded condition. Use factor slots named after their \
Factor Value labels (e.g. {{Sex}}, {{Age}}, {{Sample Dilution}}).
3. "slot_sources": for EVERY slot you used, map the slot name to \
{{"type": "...", "field": "..."}}. Allowed types and their field meanings:
   - "organism"  (field ignored)
   - "tissue"    (field ignored)
   - "variant"   (field ignored)
   - "sample_type" (field ignored; when the sample type is a QC / reference / \
dilution / blank sample, the runtime uses the \"qc_string\" instead)
   - "factor"    (field = the Factor Value label, e.g. \"Gender\", \"Age\")
   - "characteristic" (field = the Characteristics label)
   - "code"      (field = \"name\" or \"data_files\"; resolves the decoded \
disease/group for THIS sample from its sample name and/or its data file names)
4. "qc_string": the phrase to use for QC / reference / dilution / blank \
samples (e.g. \"quality control sample\").
5. "study_context": one short phrase describing the study's biological \
setting (not repeated per sample).

RULES:
- Every slot in the template must have an entry in slot_sources.
- Sentence must be sample-specific and include the disease/condition when it \
can be determined for that sample.
- Do NOT include: sample IDs, study accession, instrument models, analytical \
techniques, file formats.
- Prefer natural, concise biomedical phrasing."""


def build_study_profile_prompt(candidate: StudyCandidate) -> str:
    """Build the ONE prompt the agent's LLM uses to author the study recipe."""
    title = (candidate.title or "")[:200]
    abstract = _clean_html(candidate.description or "")
    contexts = collect_sample_contexts(candidate)

    columns = ", ".join(candidate.sample_metadata_fields or []) or "none"

    # Example rows: show up to 3 full sample rows
    rows = []
    for row in (candidate.sample_metadata or [])[:3]:
        lines = ", ".join(f"{k}={v}" for k, v in row.items() if v)
        rows.append(lines)
    example_rows = "\n".join(rows) or "none"

    # Distinct data-file stems
    stems: set[str] = set()
    for ctx in contexts:
        for f in ctx.raw_data_files + ctx.derived_data_files:
            stems.add(Path(f).stem)
    file_stems = ", ".join(sorted(stems)[:150]) or "none"

    # Factor summary
    all_factors: dict[str, set[str]] = {}
    for ctx in contexts:
        for k, v in ctx.factors.items():
            all_factors.setdefault(k, set()).add(v)
    factor_lines = []
    for k in sorted(all_factors):
        vals = sorted(all_factors[k])[:30]
        factor_lines.append(f"{k}: {', '.join(vals)}")
    factor_summary = "\n".join(factor_lines) or "none"

    # Use .replace() (not .format) so literal {slots} in the template survive.
    return (STUDY_PROFILE_PROMPT
            .replace("__COLUMNS__", columns)
            .replace("__EXAMPLE_ROWS__", example_rows)
            .replace("__FILE_STEMS__", file_stems)
            .replace("__FACTOR_SUMMARY__", factor_summary)
            .replace("__TITLE__", title)
            .replace("__ABSTRACT__", abstract or "not available"))


# ── Recipe application (deterministic, zero per-sample LLM) ────────


def parse_study_profile(llm_json: str) -> StudyProfile:
    """Parse the LLM's JSON response into a :class:`StudyProfile`.

    Tolerates fenced/marked code blocks.
    """
    text = llm_json.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    data = json.loads(text)
    return StudyProfile(
        codes=data.get("codes", {}) or {},
        sentence_template=data.get("sentence_template", ""),
        slot_sources=data.get("slot_sources", {}) or {},
        qc_string=data.get("qc_string", "quality control sample") or "quality control sample",
        study_context=data.get("study_context", ""),
    )


def apply_recipe(
    context: SampleContext,
    profile: StudyProfile,
) -> SampleDescription:
    """Deterministically fill the recipe template for one sample."""
    # QC/reference/dilution/blank sample type -> use qc_string
    is_qc = _is_qc(context.sample_type)

    values: dict[str, str] = {}
    used: list[str] = []

    for slot, spec in (profile.slot_sources or {}).items():
        typ = (spec or {}).get("type", "")
        field = (spec or {}).get("field", "")
        val = _resolve_slot(context, profile, typ, field, is_qc)
        if val:
            values[slot] = val
            used.append(f"{slot}={val}")

    sentence = _fill_template(profile.sentence_template, values, profile, is_qc)
    return SampleDescription(
        study_id=context.study_id,
        sample_name=context.sample_name,
        sentence=sentence,
        used_sources=used,
    )


def _resolve_slot(
    ctx: SampleContext,
    profile: StudyProfile,
    typ: str,
    field: str,
    is_qc: bool,
) -> str:
    if typ == "organism":
        return ctx.organism
    if typ == "tissue":
        return ctx.tissue
    if typ == "variant":
        return ctx.variant
    if typ == "sample_type":
        return profile.qc_string if is_qc else ctx.sample_type
    if typ == "qc":
        return profile.qc_string if is_qc else ""
    if typ == "factor":
        return ctx.factors.get(field, "") or ctx.characteristics.get(field, "")
    if typ == "characteristic":
        return ctx.characteristics.get(field, "")
    if typ == "code":
        return _decode_code(ctx, profile, field)
    return ""


def _decode_code(ctx: SampleContext, profile: StudyProfile, field: str) -> str:
    """Decode the disease/group code for a sample from its name/data files."""
    haystacks: list[str] = [ctx.sample_name]
    if field in ("data_files", ""):
        haystacks += list(ctx.raw_data_files) + list(ctx.derived_data_files)
    found: list[str] = []
    for code, meaning in (profile.codes or {}).items():
        cl = code.lower()
        if not cl:
            continue
        for h in haystacks:
            hl = h.lower()
            tokens = set(re.split(r"[_\-./\s]+", hl))
            if cl in tokens or (len(cl) >= 3 and cl in hl):
                found.append(meaning)
                break
    return "; ".join(found)


def _fill_template(
    template: str,
    values: dict[str, str],
    profile: StudyProfile,
    is_qc: bool,
) -> str:
    s = template or ""
    for k, v in values.items():
        s = s.replace("{" + k + "}", v)
    # Drop any unfilled slots and clean whitespace
    s = re.sub(r"\{[^}]*\}", "", s)
    s = re.sub(r"\s{2,}", " ", s).replace(" ,", ",").strip()
    if not s.endswith("."):
        s += "."
    return s


def _is_qc(sample_type: str) -> bool:
    t = (sample_type or "").lower()
    return any(k in t for k in [
        "qc", "quality control", "reference", "dilution", "blank", "pooled qc",
    ])


# ── Disk cache (so large studies are generated once) ───────────────


class SampleSentencesStore:
    """File-backed cache of generated sample descriptions, keyed by study."""

    def __init__(self, path: str | Path = ".agents/sample_cache.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    @staticmethod
    def key_for(candidate: StudyCandidate) -> str:
        m = hashlib.sha256()
        m.update(candidate.study_id.encode())
        for row in candidate.sample_metadata or []:
            m.update(repr(sorted(row.items())).encode())
        return f"{candidate.study_id}:{m.hexdigest()[:16]}"

    def load(self, study_key: str) -> list[SampleDescription] | None:
        raw = self._data.get(study_key)
        if raw is None:
            return None
        return [
            SampleDescription(
                study_id=r["study_id"],
                sample_name=r["sample_name"],
                sentence=r["sentence"],
                used_sources=r.get("used_sources", []),
            )
            for r in raw
        ]

    def save(self, study_key: str, descriptions: list[SampleDescription]) -> None:
        self._data[study_key] = [
            {
                "study_id": d.study_id,
                "sample_name": d.sample_name,
                "sentence": d.sentence,
                "used_sources": d.used_sources,
            }
            for d in descriptions
        ]
        self.path.write_text(json.dumps(self._data, indent=2))

    def __contains__(self, study_key: str) -> bool:
        return study_key in self._data


# ── Helpers ────────────────────────────────────────────────────────


def _first_term(terms: list[Any]) -> str:
    for t in terms:
        if hasattr(t, "term") and t.term:
            return t.term
        if isinstance(t, dict) and t.get("term"):
            return t["term"]
    return ""


def _get_char(row: dict[str, str], key: str) -> str:
    for col, val in row.items():
        if key in col and "Characteristics[" in col:
            if val and val.strip():
                return val.strip()
    return ""


def _extract_bracketed(row: dict[str, str], prefix: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for col, val in row.items():
        if prefix in col:
            label = col.replace(prefix, "").rstrip("]").strip()
            v = (val or "").strip()
            if v:
                out[label] = v
    return out


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text