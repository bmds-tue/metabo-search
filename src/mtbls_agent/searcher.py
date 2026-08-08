"""Phase 1: broad search against the MetaboLights v2 API."""

from __future__ import annotations

from typing import Any

from mtbls_agent.client import parse_hit, search_studies_raw
from mtbls_agent.models import StudyCandidate


def search_studies(
    query: str = "",
    *,
    page_size: int = 100,
    filters: list[dict[str, Any]] | None = None,
    sort_field: str | None = None,
    sort_direction: str = "desc",
    ms_filters: dict[str, Any] | None = None,
    organism: str | list[str] | None = None,
    technique: str | list[str] | None = None,
    sample_type: str | list[str] | None = None,
    min_samples: int | None = None,
    min_raw_files: int | None = None,
    max_results: int = 200,
) -> list[StudyCandidate]:
    """Broad search returning shallow ``StudyCandidate`` objects.

    This is **Phase 1** — get many candidates quickly from the search index.
    Results contain search-level metadata (organisms, techniques, sample count,
    file counts, description, publications …) but **not** full ISA-Tab detail.

    Parameters
    ----------
    query : str
        Free-text search (e.g. ``"lipidomics human blood plasma"``).
    page_size : int
        Results per API page (max 100).  Use 100 for broad recall.
    filters : list[dict] | None
        API-level filter objects.  If ``organism`` / ``technique`` /
        ``sample_type`` shortcuts are also given, they are merged.
    sort_field : str | None
        Sort by field (``"sampleCount"``, ``"submissionDate"``, …).
    sort_direction : str
        ``"asc"`` or ``"desc"``.
    ms_filters : dict | None
        Mass-spec specific filters.
    organism : str | list[str] | None
        Shortcut: filter by organism term (e.g. ``"Homo sapiens"``).
    technique : str | list[str] | None
        Shortcut: filter by assay technique (e.g. ``"LC-MS"``).
    sample_type : str | list[str] | None
        Shortcut: filter by sample type / organism part (e.g. ``"blood plasma"``).
    min_samples : int | None
        Shortcut: minimum sample count.
    min_raw_files : int | None
        Shortcut: minimum raw file count.
    max_results : int
        Maximum number of candidates to return (pagination across pages).

    Returns
    -------
    list[StudyCandidate]
        Shallow candidates — call ``inspect_studies`` on promising ones.
    """
    # Build API-level filters from shortcuts
    shortcut_filters: list[dict[str, Any]] = []
    if organism:
        org_values = [organism] if isinstance(organism, str) else organism
        shortcut_filters.append(
            {"field": "organisms.term", "values": org_values, "operator": "any"}
        )
    if technique:
        tech_values = [technique] if isinstance(technique, str) else technique
        shortcut_filters.append(
            {"field": "assayTechniques.name", "values": tech_values, "operator": "any"}
        )
    if sample_type:
        st_values = [sample_type] if isinstance(sample_type, str) else sample_type
        shortcut_filters.append(
            {"field": "organismParts.term", "values": st_values, "operator": "any"}
        )

    # Merge user-supplied filters with shortcuts
    all_filters = (filters or []) + shortcut_filters

    candidates: list[StudyCandidate] = []
    page = 1
    seen_ids: set[str] = set()

    while len(candidates) < max_results:
        resp = search_studies_raw(
            query=query,
            page=page,
            page_size=min(page_size, 100),
            filters=all_filters or None,
            sort_field=sort_field,
            sort_direction=sort_direction,
            ms_filters=ms_filters,
        )

        content = resp.get("content", {})
        results = content.get("results", [])
        total = content.get("totalResults", 0)

        if not results:
            break

        for hit in results:
            sid = hit.get("studyId", "")
            if sid in seen_ids:
                continue
            seen_ids.add(sid)

            data = parse_hit(hit)
            candidate = StudyCandidate(**data)

            # Apply post-filter on min_samples / min_raw_files (API can't do these natively)
            if min_samples is not None and (
                candidate.sample_count is None or candidate.sample_count < min_samples
            ):
                continue
            if min_raw_files is not None and (
                candidate.raw_file_count is None or candidate.raw_file_count < min_raw_files
            ):
                continue

            candidates.append(candidate)
            if len(candidates) >= max_results:
                break

        # Pagination: stop if we've exhausted all results
        if page * page_size >= total:
            break
        page += 1

    return candidates




def profile_to_search_args(profile) -> dict:
    """Deterministically map a profile's HARD requirements to search kwargs,
    so the search API pre-filters (server-side) instead of fetching everything.

    Only criteria the API/filter can express are mapped: organism, technique,
    sample_type, min_samples.  Everything else is enforced later (shallow or
    deep screening).
    """
    hard = profile.hard
    args: dict = {}
    if hard.organisms:
        args["organism"] = hard.organisms
    if hard.techniques:
        args["technique"] = hard.techniques
    if hard.sample_types:
        args["sample_type"] = hard.sample_types
    if hard.min_samples is not None:
        args["min_samples"] = hard.min_samples
    return args


__all__ = ["search_studies", "profile_to_search_args"]