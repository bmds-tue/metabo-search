"""Pydantic models for the mtbls-agent library."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ──────────────────────────────────────────────
# Requirement models
# ──────────────────────────────────────────────


@dataclass
class StudyRequirements:
    """A set of requirements (used for both hard and nice-to-have)."""

    organisms: list[str] | None = None
    sample_types: list[str] | None = None
    techniques: list[str] | None = None
    ionization_modes: list[str] | None = None
    analysis_types: list[str] | None = None
    instrument_models: list[str] | None = None
    data_formats: list[str] | None = None
    min_samples: int | None = None
    has_raw_data: bool | None = None
    has_derived_data: bool | None = None

    def __bool__(self) -> bool:
        """True if any field is set."""
        return any(v is not None for v in self.__dict__.values())


@dataclass
class RequirementProfile:
    """What a researcher is looking for.

    - ``hard``: non-negotiable filters (pass/fail)
    - ``nice_to_have``: scored criteria (higher = better)
    - ``free_text``: natural language description used for relevance boosting
    """

    hard: StudyRequirements = field(default_factory=StudyRequirements)
    nice_to_have: StudyRequirements = field(default_factory=StudyRequirements)
    free_text: str = ""


# ──────────────────────────────────────────────
# Study information models (Phase 1 & Phase 2)
# ──────────────────────────────────────────────


@dataclass
class OntologyTerm:
    """An ontology-annotated term (organism, tissue, …)."""

    term: str
    term_source_ref: str = ""
    term_accession_number: str = ""


@dataclass
class AssayInfo:
    """Details about a single assay within a study."""

    technique_name: str = ""
    technique_main: str = ""
    technique_sub: str = ""
    instrument: str = ""
    column_type: str = ""
    ionization_mode: str = ""
    raw_file_count: int = 0
    derived_file_count: int = 0
    measurement_type: str = ""


@dataclass
class DataFileInfo:
    """A data file and its metadata."""

    path: str
    size_bytes: int
    format: str = ""
    assay_ref: str = ""


@dataclass
class PublicationInfo:
    """A publication linked to the study."""

    doi: str = ""
    pubmed_id: str = ""
    title: str = ""
    author_list: str = ""
    status: str = ""


@dataclass
class ProtocolInfo:
    """A protocol used in the study."""

    name: str = ""
    description: str = ""
    protocol_type: str = ""


@dataclass
class StudyCandidate:
    """All known information about a MetaboLights study.

    Phase 1 (shallow) fields come from the search API.
    Phase 2 (deep) fields are populated after downloading + parsing ISA files.
    """

    # ── Identity ──
    study_id: str
    title: str = ""
    description: str = ""
    status: str = ""

    # ── Phase 1: search API information ──
    organisms: list[OntologyTerm] = field(default_factory=list)
    organism_parts: list[OntologyTerm] = field(default_factory=list)
    assay_techniques: list[dict] = field(default_factory=list)
    design_descriptors: list[OntologyTerm] = field(default_factory=list)
    technology_types: list[OntologyTerm] = field(default_factory=list)
    factors: list[OntologyTerm] = field(default_factory=list)

    sample_count: int | None = None
    raw_file_count: int | None = None
    derived_file_count: int | None = None
    assay_count: int | None = None
    size_in_bytes: int | None = None

    publications: list[PublicationInfo] = field(default_factory=list)
    contacts: list[str] = field(default_factory=list)
    submitters: list[str] = field(default_factory=list)

    submission_date: str = ""
    public_release_date: str = ""

    # ── Phase 2: deep-inspection information ──
    assays: list[AssayInfo] = field(default_factory=list)
    data_files: list[DataFileInfo] = field(default_factory=list)
    protocols: list[ProtocolInfo] = field(default_factory=list)
    sample_metadata_fields: list[str] = field(default_factory=list)
    sample_metadata: list[dict[str, str]] = field(default_factory=list)
    metabolite_count: int | None = None
    metadata_completeness: float = 0.0
    investigation_file_parsed: bool = False
    assay_files_parsed: bool = False
    sample_file_parsed: bool = False
    maf_files_parsed: bool = False

    # Maps sample_name -> {"raw": [...], "derived": [...]} data file paths
    # Populated from assay files' Raw/Derived Spectral Data File columns.
    sample_file_map: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    # ── Cached API raw data ──
    _raw_api_result: dict[str, Any] = field(default_factory=dict)

    @property
    def inspection_depth(self) -> str:
        """'shallow' if only Phase 1 data, 'deep' if ISA files were inspected."""
        return "deep" if self.investigation_file_parsed else "shallow"


# ──────────────────────────────────────────────
# Scoring models
# ──────────────────────────────────────────────


@dataclass
class FitnessScore:
    """How well a study matches a requirement profile."""

    overall: float = 0.0  # 0-1
    hard_passed: bool = False
    hard_fail_reasons: list[str] = field(default_factory=list)
    per_criterion: dict[str, float] = field(default_factory=dict)
    criterion_explanations: dict[str, str] = field(default_factory=dict)


@dataclass
class ScoredCandidate:
    """A study candidate with its fitness score."""

    candidate: StudyCandidate
    score: FitnessScore

    # Convenience
    @property
    def study_id(self) -> str:
        return self.candidate.study_id

    @property
    def title(self) -> str:
        return self.candidate.title


# ──────────────────────────────────────────────
# Output models
# ──────────────────────────────────────────────


@dataclass
class ComparisonReport:
    """Structured comparison of scored studies."""

    candidates: list[ScoredCandidate]
    table_columns: list[str] = field(default_factory=list)
    table_rows: list[list[str | float | bool]] = field(default_factory=list)
    query_profile: RequirementProfile | None = None

    @property
    def top_candidate(self) -> ScoredCandidate | None:
        return self.candidates[0] if self.candidates else None

    def as_rows(self) -> list[dict[str, Any]]:
        """Return rows as dicts keyed by column name."""
        return [
            dict(zip(self.table_columns, row)) for row in self.table_rows
        ]