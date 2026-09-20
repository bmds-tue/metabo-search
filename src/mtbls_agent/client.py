"""HTTP client for the MetaboLights v2 search API."""

from __future__ import annotations

from typing import Any

import httpx

API_BASE = "https://www.ebi.ac.uk/metabolights/ws3"
SEARCH_URL = f"{API_BASE}/public/v2/public-study-index/search"
REQUEST_TIMEOUT = 30  # seconds


def search_studies_raw(
    *,
    query: str = "",
    page: int = 1,
    page_size: int = 20,
    filters: list[dict[str, Any]] | None = None,
    sort_field: str | None = None,
    sort_direction: str = "desc",
    ms_filters: dict[str, Any] | None = None,
    include_all_ids: bool = False,
) -> dict[str, Any]:
    """Call the v2 study search API and return the raw JSON response.

    Parameters
    ----------
    query : str
        Free-text search query.
    page : int
        1-indexed page number.
    page_size : int
        Results per page (max 100).
    filters : list[dict] | None
        List of ``{"field": str, "values": list, "operator": "all"|"any"|"none"}``.
    sort_field : str | None
        Field to sort on (e.g. ``"sampleCount"``, ``"submissionDate"``).
    sort_direction : str
        ``"asc"`` or ``"desc"``.
    ms_filters : dict | None
        Mass-spec specific filters:
        ``{"instrument": [...], "column_type": [...], "chromatography_instrument": [...], "operator": "and"|"or"}``.
    include_all_ids : bool
        If True, response includes ``all_study_ids`` for all matches.
    """
    body: dict[str, Any] = {
        "page": {"current": page, "size": page_size},
    }

    if query:
        body["query"] = query

    if filters:
        body["filters"] = filters

    if sort_field:
        body["sort"] = {"field": sort_field, "direction": sort_direction}

    if ms_filters:
        body["ms"] = ms_filters

    params = {}
    if include_all_ids:
        params["include_all_ids"] = "true"

    with httpx.Client(timeout=REQUEST_TIMEOUT) as http:
        resp = http.post(SEARCH_URL, params=params, json=body)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

    if data.get("status") != "success":
        msg = data.get("errorMessage", data.get("successMessage", "Unknown error"))
        raise RuntimeError(f"Search API error: {msg}")

    return data


def parse_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Flatten a raw API hit into a cleaner dict for constructing a StudyCandidate."""
    from mtbls_agent.models import OntologyTerm, PublicationInfo

    def _to_ontology(items: list) -> list[OntologyTerm]:
        return [
            OntologyTerm(
                term=t.get("term", ""),
                term_source_ref=t.get("termSourceRef", ""),
                term_accession_number=t.get("termAccessionNumber", ""),
            )
            for t in items if isinstance(t, dict)
        ]

    organisms = hit.get("organisms", [])
    organism_parts = hit.get("organismParts", [])
    design_descriptors = hit.get("designDescriptors", [])
    technology_types = hit.get("technologyTypes", [])
    factors_list = hit.get("factors", [])

    assays_raw = hit.get("assays", []) or []
    assay_techniques_raw = hit.get("assayTechniques", []) or []

    publications_raw = hit.get("publications", []) or []

    from mtbls_agent.models import OntologyTerm, PublicationInfo

    def _to_ontology(items: list) -> list[OntologyTerm]:
        return [
            OntologyTerm(
                term=t.get("term", ""),
                term_source_ref=t.get("termSourceRef", ""),
                term_accession_number=t.get("termAccessionNumber", ""),
            )
            for t in items if isinstance(t, dict)
        ]

    return {
        "study_id": hit.get("studyId", ""),
        "title": hit.get("title", ""),
        "description": hit.get("description", ""),
        "status": hit.get("status", ""),
        "organisms": _to_ontology(organisms),
        "organism_parts": _to_ontology(organism_parts),
        "assay_techniques": assay_techniques_raw,
        "design_descriptors": _to_ontology(design_descriptors),
        "technology_types": _to_ontology(technology_types),
        "factors": _to_ontology(factors_list),  # canonical (was raw dicts →
        #   unstable JSON round-trip broke cache keys)
        "sample_count": hit.get("sampleCount"),
        "raw_file_count": hit.get("rawFileCount"),
        "derived_file_count": hit.get("derivedFileCount"),
        "assay_count": hit.get("assayCount"),
        "size_in_bytes": hit.get("sizeInBytes"),
        "publications": [
            PublicationInfo(
                doi=p.get("doi", ""),
                pubmed_id=p.get("pubMedId", ""),
                title=p.get("title", ""),
                author_list=p.get("authorList", ""),
                status=(p.get("status") or {}).get("term", ""),
            )
            for p in publications_raw
        ],
        "contacts": [
            f"{c.get('firstName','')} {c.get('lastName','')}".strip()
            for c in (hit.get("contacts", []) or [])
        ],
        "submitters": [
            f"{s.get('firstName','')} {s.get('lastName','')}".strip()
            for s in (hit.get("submitters", []) or [])
        ],
        "submission_date": hit.get("submissionDate", ""),
        "public_release_date": hit.get("publicReleaseDate", ""),
        "_raw_api_result": hit,
    }