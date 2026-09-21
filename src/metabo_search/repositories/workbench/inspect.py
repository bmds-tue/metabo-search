"""Deep inspection of workbench candidates — parallel REST fetch + normalize.

For each study: GET `summary`, `factors`, `analysis`, `metabolites`
(~4 tiny JSON calls) on a thread pool (keep-alive client shared).  Results
are merged into the existing ``StudyCandidate`` deep fields so scoring,
describe, and manifest work unchanged.

Fixtures mode: ``load_payload(study_id, kind)`` returns JSON from
``<fixtures>/study_<id>_<kind>.json`` when the env flag is set (offline).
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from metabo_search.models import AssayInfo, StudyCandidate

PayloadLoader = Callable[[str, str], dict]


def _fetch_study(c: StudyCandidate, load: PayloadLoader) -> None:
    """Fetch + merge one study's deep metadata (mutation of the copy)."""
    sid = c.study_id
    try:
        load(sid, "summary")      # validated; the record's fields we did not
        factors = load(sid, "factors")      # use in v1 live in the corpus
        analysis = load(sid, "analysis")
        metabolites = load(sid, "metabolites")
    except Exception:  # noqa: BLE001 — soft failure by design (step plans)
        return  # keep the shallow version — the deep pass is a soft upgrade

    c.sample_metadata = _factors_rows(factors)
    c.sample_metadata_fields = sorted(
        {k for row in c.sample_metadata for k in row})
    if c.sample_metadata:
        c.sample_count = len(c.sample_metadata)
        # the sample sheet is KNOWN (factor rows) — drives describe/manifest
        # (collect_sample_contexts gates on this flag).  No ISA files exist
        # for workbench studies, but the samples themselves do.
        c.sample_file_parsed = True
    c.assays = _analysis_rows(analysis)
    c.metabolite_count = _metabolite_count(metabolites)
    # deep marker (drives inspection_depth); no ISA files by definition
    c.investigation_file_parsed = True


def _factors_rows(factors: dict) -> list[dict[str, str]]:
    """Normalize an /factors payload into per-sample rows.

    The row keeps its native lowercase keys (``sample_name``, ``factors``,
    …) so repository-specific consumers keep working, and ALSO carries
    ISA-compatible aliases (``Sample Name`` / ``Source Name`` /
    ``Factor Value[<label>]``) so the shared describe/manifest consumers
    resolve names and factors without special-casing the repository.
    """
    rows = []
    for row in factors.values():
        if not isinstance(row, dict):
            continue
        sample_name = row.get("local_sample_id") or row.get("mb_sample_id") or ""
        base = {
            "sample_name": sample_name,
            "mb_sample_id": row.get("mb_sample_id", ""),
            "sample_source": row.get("sample_source", ""),
            "factors": row.get("factors", ""),
        }
        aliases = {
            "Source Name": sample_name,
            "Sample Name": sample_name,
        }
        for label, value in (_parse_factors(row.get("factors", ""))).items():
            aliases[f"Factor Value[{label}]"] = value
        rows.append({**base, **aliases})
    return rows


def _parse_factors(value: str) -> dict[str, str]:
    """Parse the workbench combined factors string into label → value.

    The /factors payload packs factors as ::

        "Arabidopsis Genotype:fatb-ko KD; At1g08510 |"
        " Plant Wounding Treatment:Control - Non-Wounded"

    Groups are `` | `` separated, label:value pairs ``; `` separated; pieces
    without a colon (bare accessions) are ignored.
    """
    out: dict[str, str] = {}
    if not value:
        return out
    for group in str(value).split("|"):
        for piece in group.split(";"):
            label, sep, val = piece.partition(":")
            label, val = label.strip(), val.strip()
            if sep and label and val:
                out[label] = val
    return out


def _analysis_rows(analysis) -> list[AssayInfo]:
    """Normalize an /analysis payload (single record or keyed rows)."""
    if isinstance(analysis, dict) and analysis.get("analysis_id"):
        records = [analysis]                     # single-record form
    else:
        records = [v for v in analysis.values() if isinstance(v, dict)]
    out = []
    for row in records:
        out.append(AssayInfo(
            technique_name=row.get("analysis_type", ""),
            technique_main=row.get("analysis_type", ""),
            instrument=row.get("ms_instrument_name", ""),
            ionization_mode=row.get("ion_mode", "") or row.get("ion_mode"),
            measurement_type=row.get("ms_type", ""),
        ))
    return out


def _metabolite_count(metabolites: dict) -> int | None:
    """Identified-metabolite LIST length from /metabolites.

    PROXY ONLY — the payload rows are ``(study_id, analysis_id,
    analysis_summary, metabolite_name, refmet_name)`` with NO per-sample
    abundance columns, so this is not a signal-matrix dimension.  The m×s
    matrix lives in mwTab datatables (``metabolite_table`` is a stub until
    the MAF-adapter milestone); quote this number as list length, and prefer
    MetaboLights MAF counts when the matrix dimension itself matters.
    """
    n = 0
    for row in metabolites.values():
        if isinstance(row, dict) and row.get("metabolite_name"):
            n += 1
    return n or None


def deep_metadata(candidates: list[StudyCandidate], *, workers: int,
                  load_payload: PayloadLoader) -> list[StudyCandidate]:
    """Deep-inspect candidates in parallel, order preserved.

    Each input candidate is deep-copied; deep fields are merged into the
    copy.  A fetch failure leaves the shallow version in place (soft
    failure) — matching the MetaboLights behavior of keeping shallow copies.
    """
    out: list[StudyCandidate] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [(c, pool.submit(_fetch_study, c, load_payload))
                for c in candidates]
        for c, fut in futs:
            fut.result()  # raises propagate — a hard bug, not a soft item
            out.append(c)
    return out


def sample_rows(candidate: StudyCandidate) -> list[dict[str, str]]:
    """Per-sample rows for describe/manifest (from factors)."""
    return list(candidate.sample_metadata or [])