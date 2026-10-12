"""Pydantic models for the Metabolomics Workbench REST API.

The Workbench API has no OpenAPI document, so these models follow the recorded
response fixtures. Unknown fields are retained (``extra="allow"``).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _null_to_empty_str(value: object) -> object:
    """Coerce a JSON null to an empty string."""
    return "" if value is None else value


def _null_to_empty_list(value: object) -> object:
    """Coerce a JSON null to an empty list."""
    return [] if value is None else value


def _int_or_none(value: object) -> object:
    """Coerce an empty string or null to ``None`` before int parsing."""
    return None if value is None or value == "" else value


Str = Annotated[str, BeforeValidator(_null_to_empty_str)]
Strings = Annotated[list[str], BeforeValidator(_null_to_empty_list)]
Int = Annotated[int | None, BeforeValidator(_int_or_none)]


class WorkbenchModel(BaseModel):
    """Base model: alias-friendly, frozen, retains unknown fields."""

    model_config = ConfigDict(populate_by_name=True, extra="allow", frozen=True)


class StudySummary(WorkbenchModel):
    """A per-study summary record (the whole-index search unit)."""

    study_id: Str = ""
    study_title: Str = ""
    species: Str = ""
    institute: Str = ""
    analysis_type: Str = ""
    number_of_samples: Int = None
    submission_date: Str = ""
    release_date: Str = ""
    version: Str = ""
    revision_no: Str = ""
    revision_datetime: Str = ""
    revision_comment: Str = ""
    license: Str = ""
    license_url: Str = ""
    study_url: Str = ""


class SampleRow(WorkbenchModel):
    """One row of a study's ``factors`` response.

    ``factors`` is the API's packed string (``"Label:value; Label2:value2 | …"``);
    ``factor_values`` is its parsed form.
    """

    study_id: Str = ""
    local_sample_id: Str = ""
    mb_sample_id: Str = ""
    sample_source: Str = ""
    factors: Str = ""
    raw_data: Str = ""
    factor_values: dict[str, str] = {}


class AnalysisRecord(WorkbenchModel):
    """One analysis of a study (MS or NMR)."""

    study_id: Str = ""
    analysis_id: Str = ""
    analysis_summary: Str = ""
    analysis_type: Str = ""
    chromatography_system: Str = Field(default="", alias="chromatography system")
    column_name: Str = ""
    chromatography_type: Str = ""
    ms_instrument_name: Str = ""
    ms_instrument_type: Str = ""
    ms_type: Str = ""
    ion_mode: Str = ""
    nmr_instrument_type: Str = ""
    nmr_experiment_type: Str = ""
    spectrometer_frequency: Str = ""
    nmr_solvent: Str = ""
    units: Str = ""


class MetaboliteRow(WorkbenchModel):
    """One identified metabolite of a study (no per-sample abundances)."""

    study_id: Str = ""
    analysis_id: Str = ""
    analysis_summary: Str = ""
    metabolite_name: Str = ""
    refmet_name: Str = ""


class StudyDetail(WorkbenchModel):
    """Deep, parsed study data: factors, analyses, and identified metabolites.

    ``error`` is non-empty when inspection failed after retries; the other
    fields are then empty. A failed study is still returned, so a batch
    inspection always has one result per input.
    """

    study_id: Str = ""
    summary: StudySummary | None = None
    samples: list[SampleRow] = []
    analyses: list[AnalysisRecord] = []
    metabolites: list[MetaboliteRow] = []
    error: Str = ""
