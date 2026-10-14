"""Pydantic models for the MetaboLights public study index.

Field names are snake_case; the API's camelCase is declared as an alias, so
``model_dump(by_alias=True)`` round-trips to the wire format. Unknown fields are
retained (``extra="allow"``) so upstream additions are never silently dropped;
a contract test flags them.

The study document returned by ``/public/v2/public-study-index/search`` is the
source of truth for study-level data. Deep sample/metabolite data comes from ISA
files and MAF, parsed separately.
"""

from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

T = TypeVar("T")


def _null_to_empty_str(value: object) -> object:
    """Coerce a JSON null to an empty string."""
    return "" if value is None else value


def _null_to_empty_list(value: object) -> object:
    """Coerce a JSON null to an empty list."""
    return [] if value is None else value


Str = Annotated[str, BeforeValidator(_null_to_empty_str)]
"""A string that tolerates a JSON null."""

Strings = Annotated[list[str], BeforeValidator(_null_to_empty_list)]
"""A list of strings that tolerates a JSON null."""


class MetaboLightsModel(BaseModel):
    """Base model: alias-friendly, frozen, retains unknown fields."""

    model_config = ConfigDict(populate_by_name=True, extra="allow", frozen=True)


class OntologyTerm(MetaboLightsModel):
    """An ontology-annotated term (organism, factor, descriptor, …)."""

    term: Str = ""
    term_source_ref: Str = Field(default="", alias="termSourceRef")
    term_accession_number: Str = Field(default="", alias="termAccessionNumber")


def _terms(value: object) -> object:
    return [] if value is None else value


TermList = Annotated[list[OntologyTerm], BeforeValidator(_terms)]
"""A list of ontology terms that tolerates a JSON null."""


class AssayTechnique(MetaboLightsModel):
    """A technique label with its main/sub classification."""

    name: Str = ""
    main: Str = ""
    sub: Str = ""
    technique: Str = ""


class Assay(MetaboLightsModel):
    """One assay in the study's assay table."""

    file_path: Str = Field(default="", alias="filePath")
    technology_platform: Str = Field(default="", alias="technologyPlatform")
    measurement_type: OntologyTerm = Field(default=OntologyTerm(), alias="measurementType")
    technology_type: OntologyTerm = Field(default=OntologyTerm(), alias="technologyType")
    technique: AssayTechnique = Field(default=AssayTechnique())
    assignment_files: Strings = Field(default=[], alias="assignmentFiles")


class FactorPair(MetaboLightsModel):
    """One factor value observed in the study (name, value)."""

    name: Str = ""
    value: Str = ""


class Publication(MetaboLightsModel):
    """A linked publication."""

    title: Str = ""
    author_list: Str = Field(default="", alias="authorList")
    pubmed_id: Str = Field(default="", alias="pubMedId")
    doi: Str = ""
    status: OntologyTerm = Field(default=OntologyTerm())


class ProtocolComponent(MetaboLightsModel):
    """A component of a protocol (e.g. an instrument)."""

    name: Str = ""
    type: Str = ""
    term_source_ref: Str = Field(default="", alias="termSourceRef")
    term_accession_number: Str = Field(default="", alias="termAccessionNumber")


class ProtocolParameter(MetaboLightsModel):
    """A parameter of a protocol."""

    term: Str = ""
    term_source_ref: Str = Field(default="", alias="termSourceRef")
    term_accession_number: Str = Field(default="", alias="termAccessionNumber")


class Protocol(MetaboLightsModel):
    """A study protocol."""

    name: Str = ""
    description: Str = ""
    uri: Str = ""
    version: Str = ""
    protocol_type: OntologyTerm = Field(default=OntologyTerm(), alias="protocolType")
    components: list[ProtocolComponent] = Field(default=[])
    parameters: list[ProtocolParameter] = Field(default=[])


class Contact(MetaboLightsModel):
    """A study contact."""

    first_name: Str = Field(default="", alias="firstName")
    last_name: Str = Field(default="", alias="lastName")
    mid_initials: Str = Field(default="", alias="midInitials")
    email: Str = ""
    affiliation: Str = ""
    address: Str = ""
    orcid: Str = ""
    roles: TermList = Field(default=[])


class Submitter(MetaboLightsModel):
    """A study submitter (roles are plain strings)."""

    fullname: Str = ""
    email: Str = ""
    affiliation: Str = ""
    address: Str = ""
    country: Str = ""
    orcid: Str = ""
    roles: Strings = Field(default=[])


class Funding(MetaboLightsModel):
    """A funding record."""

    fund_ref_id: Str = Field(default="", alias="fundRefId")
    funder: Str = ""
    grant_identifier: Str = Field(default="", alias="grantIdentifier")


class OrganismLineage(MetaboLightsModel):
    """Taxonomic lineage of the study's organisms, one list per rank."""

    domain: Strings = Field(default=[])
    kingdom: Strings = Field(default=[])
    phylum: Strings = Field(default=[])
    klass: Strings = Field(default=[], alias="class")
    order: Strings = Field(default=[])
    family: Strings = Field(default=[])
    genus: Strings = Field(default=[])
    species: Strings = Field(default=[])


class StudyHit(MetaboLightsModel):
    """One study document from the public study index.

    This is the shallow record: identity, facets, counts, publications,
    protocols, and contacts. Sample- and metabolite-level data is not here.
    """

    study_id: Str = Field(..., alias="studyId")
    title: Str = ""
    description: Str = ""
    status: Str = ""
    study_category: Str = Field(default="", alias="studyCategory")
    curation_request: Str = Field(default="", alias="curationRequest")
    submission_date: Str = Field(default="", alias="submissionDate")
    public_release_date: Str = Field(default="", alias="publicReleaseDate")
    modified_time: Str = Field(default="", alias="modifiedTime")

    sample_count: int | None = Field(default=None, alias="sampleCount")
    assay_count: int | None = Field(default=None, alias="assayCount")
    raw_file_count: int | None = Field(default=None, alias="rawFileCount")
    derived_file_count: int | None = Field(default=None, alias="derivedFileCount")
    size_in_bytes: int | None = Field(default=None, alias="sizeInBytes")
    size_in_text: Str = Field(default="", alias="sizeInText")

    investigation_file_path: Str = Field(default="", alias="investigationFilePath")
    sample_file_path: Str = Field(default="", alias="sampleFilePath")

    organisms: TermList = Field(default=[])
    organism_parts: TermList = Field(default=[], alias="organismParts")
    sample_types: TermList = Field(default=[], alias="sampleTypes")
    technology_types: TermList = Field(default=[], alias="technologyTypes")
    design_descriptors: TermList = Field(default=[], alias="designDescriptors")
    factors: TermList = Field(default=[])
    variants: TermList = Field(default=[])

    annotations: Strings = Field(default=[])
    curator_annotations: Strings = Field(default=[], alias="curatorAnnotations")
    parse_errors: Strings = Field(default=[], alias="parseErrors")

    organism_lineage: OrganismLineage = Field(
        default=OrganismLineage(), alias="organismLineage"
    )
    raw_file_extensions: Strings = Field(default=[], alias="rawFileExtensions")
    derived_file_extensions: Strings = Field(default=[], alias="derivedFileExtensions")

    assays: list[Assay] = Field(default=[])
    assay_techniques: list[AssayTechnique] = Field(default=[], alias="assayTechniques")
    factor_pairs: list[FactorPair] = Field(default=[], alias="factorPairs")
    publications: list[Publication] = Field(default=[])
    protocols: list[Protocol] = Field(default=[])
    contacts: list[Contact] = Field(default=[])
    submitters: list[Submitter] = Field(default=[])
    fundings: list[Funding] = Field(default=[])


class Sample(MetaboLightsModel):
    """A parsed ISA sample (one row of a sample sheet)."""

    sample_name: Str = ""
    sample_row: int = 0
    source_name: Str = ""
    organism: Str = ""
    organism_part: Str = ""
    sample_type: Str = ""
    characteristics: dict[str, str] = {}
    factor_values: dict[str, str] = {}
    raw_files: Strings = []
    derived_files: Strings = []


class Metabolite(MetaboLightsModel):
    """A parsed MAF metabolite row.

    ``abundances`` maps a per-sample column name to its numeric value; the
    per-sample columns are the MAF's non-metadata columns.
    """

    study_id: Str = ""
    file: Str = ""
    row: int = 0
    database_identifier: Str = ""
    metabolite_identification: Str = ""
    chemical_formula: Str = ""
    mass_to_charge: float | None = None
    retention_time: float | None = None
    abundances: dict[str, float] = {}


class StudyFile(MetaboLightsModel):
    """A file in a study's ``FILES/`` directory."""

    study_id: Str = ""
    path: Str = ""
    name: Str = ""
    directory: Str = ""
    extension: Str = ""
    size_bytes: int = 0
    category: Str = ""  # "raw" | "derived" | "other"


class StudyDetail(MetaboLightsModel):
    """Deep, parsed study data: ISA samples plus MAF metabolites and files.

    ``error`` is non-empty when inspection failed after retries; the other
    fields are then empty. A failed study is still returned, so a batch
    inspection always has one result per input.
    """

    study_id: Str = ""
    samples: list[Sample] = []
    metabolites: list[Metabolite] = []
    files: list[StudyFile] = []
    error: Str = ""


class DownloadReport(MetaboLightsModel):
    """Outcome of a selective download."""

    dest_dir: Str = ""
    downloaded: list[StudyFile] = []
    failed: list[str] = []
    total_bytes: int = 0


class FacetBucket(MetaboLightsModel):
    """One value in a facet response."""

    value: Str = ""
    count: int | None = None


class FacetResponse(MetaboLightsModel):
    """A facet and its buckets."""

    type: Str = ""
    data: list[FacetBucket] = Field(default=[])


class SearchPage(MetaboLightsModel):
    """The content of a study-search response."""

    results: list[StudyHit] = Field(default=[])
    total_results: int = Field(0, alias="totalResults")
    request_id: Str = Field(default="", alias="requestId")
    facets: dict[str, list[FacetResponse]] = Field(default={})
    all_study_ids: list[str] | None = Field(default=None, alias="all_study_ids")


class APIResponse(MetaboLightsModel, Generic[T]):
    """The MetaboLights API envelope around every response body."""

    status: Str = "success"
    success_message: Str | None = Field(default=None, alias="successMessage")
    error_message: Str | None = Field(default=None, alias="errorMessage")
    errors: list[object] = Field(default=[])
    content: T | None = None


SearchResponse = APIResponse[SearchPage]
"""Envelope returned by the study-search endpoint."""
