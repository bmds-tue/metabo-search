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
Factor names and their unique values (challenge/group/condition codes such as \
OGTT, OLTT, PAT, SLD often live here as factor values, not just filenames):
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
   - "code"      (field = \"name\" | \"data_files\" | <factor label> | \
<characteristic label>; resolves the decoded disease/group for THIS sample. \
Codes may hide in the sample name, data file names, OR factor values (e.g. \
challenge codes OGTT/OLTT/PAT/SLD) — pick the field that carries the code, or \
use \"data_files\" to scan sample name + files + all factor values)
4. "qc_string": the phrase to use for QC / reference / dilution / blank \
samples (e.g. \"quality control sample\").
5. "study_context": one short phrase describing the study's biological \
setting (not repeated per sample).

RULES:
- Every slot in the template must have an entry in slot_sources.
- Sentence must be sample-specific and include the disease/condition when it \
can be determined for that sample.
- Do NOT include: sample IDs, subject/participant IDs, barcode/accession \
tokens, assay/plate/well positions, study accession, instrument models, \
analytical techniques, file formats.
- Do NOT include bare enumerated indices (time point 1, day 1, replicate 3, \
visit 2, batch 5) as-is. Either describe their biological meaning from the \
abstract (e.g. day 1 of fasting -> "after one day of fasting"; time point 0 \
-> "at baseline") or omit them entirely. Never emit a number without meaning.
- Prefer natural, concise biomedical phrasing over labels. Sex/age of a \
healthy subject may stay (biologically relevant); the subject number must not."""


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
    # QC / reference / dilution / blank / instrument-conditioning samples are
    # NOT biological patients. The library enforces this itself: they always
    # get the QC sentence, never a decoded disease.
    is_qc = _is_qc(context.sample_type)
    if is_qc:
        where = [p for p in (context.organism, context.tissue) if p]
        return SampleDescription(
            study_id=context.study_id,
            sample_name=context.sample_name,
            sentence=f"{', '.join(where)}, {profile.qc_string}.",
            used_sources=["qc"],
        )

    values: dict[str, str] = {}
    used: list[str] = []

    for slot, spec in (profile.slot_sources or {}).items():
        typ = (spec or {}).get("type", "")
        field = (spec or {}).get("field", "")
        val = _resolve_slot(context, profile, typ, field, is_qc=False)
        if val:
            values[slot] = val
            used.append(f"{slot}={val}")

    # Surface an unresolved disease/group code (e.g. a patient row with no
    # linked data files) instead of failing silently.
    if not values.get("disease") and "disease" in (profile.slot_sources or {}):
        used.append("disease=unresolved")

    sentence = _fill_template(profile.sentence_template, values, profile, is_qc=False)
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
        return _decode_code(ctx, profile, field, is_qc)
    return ""


def _decode_code(
    ctx: SampleContext, profile: StudyProfile, field: str, is_qc: bool = False
) -> str:
    """Decode a code for THIS sample.

    ``field`` selects where to look:
      - a factor label  -> decode that factor's value (e.g. OGTT/OLTT/PAT/SLD)
      - a characteristic label -> decode that characteristic's value
      - "name"          -> the sample name
      - "data_files" / "" / "any" -> sample name + data files + ALL factor
        values (disease/group codes often hide in any of these)
    """
    if is_qc:
        return ""
    if field and field in ctx.factors:
        haystacks = [ctx.factors[field]]
    elif field and field in ctx.characteristics:
        haystacks = [ctx.characteristics[field]]
    elif field in ("name", "sample_name"):
        haystacks = [ctx.sample_name]
    else:
        haystacks = ([ctx.sample_name] + list(ctx.raw_data_files)
                     + list(ctx.derived_data_files) + list(ctx.factors.values()))
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
    # Drop any unfilled slots
    s = re.sub(r"\{[^}]*\}", "", s)
    # Collapse stray commas/whitespace left by empty slots
    s = s.replace(", ,", ",").replace(" ,", ",")
    s = re.sub(r",{2,}", ",", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+([.,;!?])", r"\1", s)   # "age ." -> "age."
    s = s.strip(" ,")
    # Repair phrases like "from an  patient" where the slot was empty
    s = re.sub(r"\b(an|a)\s+([,.)]|$)", lambda m: m.group(2) or ".", s)
    if not s.endswith("."):
        s += "."
    return s


# Sample types that are NOT biological patient runs - always treated as QC.
_QC_TYPE_MARKERS = [
    "quality control", "qc", "reference", "dilution", "blank",
    "instrument conditioning", "instrument", "data-dependent acquisition",
    "acquisition", "emergency", "system suitability", "solvent",
    "mobile phase", "wash", "pooled qc", "peak shape", "testing",
]


def _is_qc(sample_type: str) -> bool:
    t = (sample_type or "").lower()
    return any(k in t for k in _QC_TYPE_MARKERS)


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
    def metadata_hash(candidate: StudyCandidate) -> str:
        m = hashlib.sha256()
        m.update(candidate.study_id.encode())
        for row in candidate.sample_metadata or []:
            m.update(repr(sorted(row.items())).encode())
        return m.hexdigest()[:16]

    @staticmethod
    def key_for_hash(
        study_id: str, data_hash: str, revision: int = 0
    ) -> str:
        return f"{study_id}:r{revision}:{data_hash}"

    @staticmethod
    def key_for(candidate: StudyCandidate, revision: int = 0) -> str:
        """Stable key per study; bump ``revision`` to author a new wording
        without overwriting a previous one (feedback-loop friendly)."""
        return SampleSentencesStore.key_for_hash(
            candidate.study_id,
            SampleSentencesStore.metadata_hash(candidate),
            revision,
        )

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
# ── One-round-trip orchestration (the LLM-friendly path) ───────────


@dataclass
class SampleTask:
    """Everything the agent needs for ONE study-level LLM round trip.

    Flow::

        task   = prepare_samples(deep_study, store)
        descs  = load_samples(task)                 # None if not cached
        if descs is None:
            descs = submit_samples(task, call_llm(task.profile_prompt))
    """

    study_id: str
    cache_key: str
    contexts: list[SampleContext]
    profile_prompt: str
    store: "SampleSentencesStore | None" = None
    revision: int = 0
    data_hash: str = ""


def prepare_samples(
    candidate: StudyCandidate,
    store: SampleSentencesStore | None = None,
    revision: int = 0,
) -> SampleTask:
    """Bundle everything for one study: contexts + the single LLM prompt.

    ``revision`` feeds the cache key: author a revised profile under a new
    revision so the feedback loop can compare wordings without clobbering.
    """
    return SampleTask(
        study_id=candidate.study_id,
        cache_key=store.key_for(candidate, revision=revision) if store
            else f"{candidate.study_id}:r{revision}",
        contexts=collect_sample_contexts(candidate),
        profile_prompt=build_study_profile_prompt(candidate),
        store=store,
        revision=revision,
        data_hash=store.metadata_hash(candidate) if store else "",
    )


def load_samples(task: SampleTask) -> list[SampleDescription] | None:
    """Return cached descriptions for this task, or None if not cached."""
    if task.store is not None and task.cache_key in task.store:
        return task.store.load(task.cache_key)
    return None


def submit_samples(
    task: SampleTask,
    profile_json: str,
) -> list[SampleDescription]:
    """Apply the LLM-authored profile to every sample; cache and return.

    ``profile_json`` is the raw text the agent's LLM produced in answer to
    ``task.profile_prompt``.
    """
    profile = parse_study_profile(profile_json)
    descriptions = [apply_recipe(ctx, profile) for ctx in task.contexts]
    if task.store is not None:
        task.store.save(task.cache_key, descriptions)
    return descriptions


def revise_samples(
    task: SampleTask,
    profile_json: str,
) -> SampleTask:
    """Author a revised wording under the next revision and cache it.

    Returns the *new* task (already submitted).  Read results with
    ``load_samples(new_task)``.  Old wording remains under the previous
    revision, so the user can compare without clobbering.
    """
    rev = task.revision + 1
    store = task.store
    key = store.key_for_hash(task.study_id, task.data_hash, rev) if store \
        else f"{task.study_id}:r{rev}"
    new_task = SampleTask(
        study_id=task.study_id,
        cache_key=key,
        contexts=task.contexts,
        profile_prompt=task.profile_prompt,
        store=store,
        revision=rev,
        data_hash=task.data_hash,
    )
    submit_samples(new_task, profile_json)
    return new_task




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