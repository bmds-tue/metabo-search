"""Pooled HTTP client + endpoint builders for the Metabolomics Workbench.

The workbench REST API is path-shaped:
``/rest/<context>/<input_item>/<input_value>/<output_item>[/format]``
(JSON by default).  All calls go through one keep-alive ``httpx.Client``
(module-level, shared across threads) so connection reuse dominates — the
same pattern as ``metabo_search.inspector``.

Contract notes
--------------
- ``_get`` raises ``httpx.HTTPStatusError`` on non-2xx (after the underlying
  client's retries); a malformed-but-200 response returns whatever JSON
  parsed — callers validate shape.
- Payloads are single-page (no pagination) by design of the API.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

REST_BASE = "https://www.metabolomicsworkbench.org/rest"
REQUEST_TIMEOUT = 180  # seconds — the ST/summary corpus call is slow (~90 s)


def _http_client() -> httpx.Client:
    """Shared keep-alive client (one connection pool for all requests)."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(
            timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=20.0),
            follow_redirects=True,
            headers={"User-Agent": "metabo-search/0.1 (research)"},
        )
    return _client


_client: httpx.Client | None = None


def get(path: str, **params: str) -> Any:
    """GET ``path`` on the REST base; return parsed JSON.

    Parameters
    ----------
    path : str
        URL path after ``REST_BASE`` (e.g. ``"/study/study_id/ST/summary"``).
    **params : str
        Query-string params (rare; kept for symmetry).

    Returns
    -------
    Any
        Parsed JSON response (dict / list / str).
    """
    resp = _http_client().get(f"{REST_BASE}{path}", params=params or None)
    resp.raise_for_status()
    try:
        return resp.json()
    except json.JSONDecodeError:
        return resp.text


# ── endpoint helpers ──────────────────────────────────────────────


def study_summary_st(study_id: str) -> dict[str, Any]:
    """Per-study summary record (``study/study_id/<id>/summary``)."""
    return get(f"/study/study_id/{study_id}/summary")


def study_factors(study_id: str) -> dict[str, Any]:
    """Per-study sample/factor rows (``.../<id>/factors``)."""
    return get(f"/study/study_id/{study_id}/factors")


def study_analysis(study_id: str) -> dict[str, Any]:
    """Per-study analysis records (``.../<id>/analysis``)."""
    return get(f"/study/study_id/{study_id}/analysis")


def study_metabolites(study_id: str) -> dict[str, Any]:
    """Per-study identified metabolites (``.../<id>/metabolites``)."""
    return get(f"/study/study_id/{study_id}/metabolites")


def all_studies_summary() -> dict[str, dict[str, str]]:
    """Full study-summary corpus (ALL public studies, ~2.6 MB, ~90 s)."""
    return get("/study/study_id/ST/summary")


def all_disease_map() -> dict[str, dict[str, str]]:
    """study_id → ``{"Study ID": ..., "Disease": ...}`` for all studies."""
    return get("/study/study_id/ST/disease")


def all_source_map() -> dict[str, dict[str, str]]:
    """study_id → ``{"Study ID": ..., "Sample source": ...}`` for all studies."""
    return get("/study/study_id/ST/source")


def all_species_map() -> dict[str, dict[str, str]]:
    """study_id → ``{"Study ID": ..., "Latin name": ..., "Common name": ...}``."""
    return get("/study/study_id/ST/species")


def metstat(slots: tuple[str, ...]) -> dict[str, dict[str, str]]:
    """MetStat slot search over the 8 slots (empty = wildcard).

    Parameters
    ----------
    slots : tuple[str, ...]
        Length-8 tuple: ANALYSIS/POLARITY/CHROM/SPECIES/SOURCE/DISEASE/
        KEGG/REFMET.  Filled with canonical values only; empty strings are
        wildcards.

    Returns
    -------
    dict[str, dict]
        ``{"RowN": {"study", "study_title", "species", "source",
        "disease"}}`` — empty dict/list when nothing matches.
    """
    joined = ";".join(slots)
    out = get(f"/metstat/{joined}")
    # the API returns {} or [] on no-match; normalize to dict
    return out if isinstance(out, dict) else {}