"""Phase 2: deep parallel inspection of MetaboLights studies.

Downloads ISA-Tab metadata from the FTP server and parses it to extract
rich detail: assays, protocols, sample characteristics, data files, etc.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from metabolights_utils.isatab.default.parser.investigation_parser import (
    parse_investigation_from_fs,
)
from metabolights_utils.isatab.default.parser.isa_table_parser import (
    parse_isa_table_sheet_from_fs,
)
import httpx

from mtbls_agent.models import (
    AssayInfo,
    DataFileInfo,
    ProtocolInfo,
    StudyCandidate,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────

HTTP_STUDY_BASE = "https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public"
HTTP_STUDY_BASE = "https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public"

# Regex patterns for ISA file names
RE_INVESTIGATION = re.compile(r"^i_([A-Za-z0-9]+)\.txt$", re.IGNORECASE)
RE_ASSAY = re.compile(r"^a_([A-Za-z0-9_\-]+)\.txt$", re.IGNORECASE)
RE_SAMPLES = re.compile(r"^s_([A-Za-z0-9_\-]+)\.txt$", re.IGNORECASE)
RE_MAF = re.compile(r"^m_([A-Za-z0-9_\-]+)\.tsv$", re.IGNORECASE)


# ── Public API ─────────────────────────────────────────────────────


def inspect_studies(
    candidates: list[StudyCandidate],
    max_workers: int = 10,
    tmp_dir: str | None = None,
    download_data_files: bool = False,
) -> list[StudyCandidate]:
    """Deep-inspect candidates in parallel.

    For each candidate:
    1. Download ISA-Tab metadata files from the MetaboLights FTP server
    2. Parse the investigation file → assays, protocols, publications
    3. Parse assay & sample files → sample count, characteristics, data files
    4. Enrich the candidate with all discovered information

    Parameters
    ----------
    candidates : list[StudyCandidate]
        Shallow candidates from phase 1.
    max_workers : int
        Number of parallel download+parse workers.
    tmp_dir : str | None
        Temporary directory for downloaded files.  A system temp dir is used
        by default and cleaned up after inspection.
    download_data_files : bool
        If True, also list/download actual data files (expensive).
        If False, only metadata files are downloaded.

    Returns
    -------
    list[StudyCandidate]
        Deep-inspected candidates.  Order matches input order.
    """
    if not candidates:
        return []

    study_ids = [c.study_id for c in candidates]
    id_map = {c.study_id: c for c in candidates}

    results: list[StudyCandidate] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        fut_to_sid = {
            executor.submit(
                _inspect_one, sid, tmp_dir, download_data_files
            ): sid
            for sid in study_ids
        }

        for fut in as_completed(fut_to_sid):
            sid = fut_to_sid[fut]
            try:
                enriched = fut.result()
                # Merge enriched data back into the original candidate
                base = id_map[sid]
                _merge_enriched(base, enriched)
                results.append(base)
            except Exception as e:
                logger.warning("Failed to deep-inspect %s: %s", sid, e)
                results.append(id_map[sid])  # keep shallow version

    # Re-sort to match input order
    order = {sid: i for i, sid in enumerate(study_ids)}
    results.sort(key=lambda c: order.get(c.study_id, 999))
    return results


# ── Internal ───────────────────────────────────────────────────────


def load_study_from_isa(
    study_id: str,
    isa_dir: str | Path,
) -> StudyCandidate:
    """Reconstruct a deep-inspected StudyCandidate from LOCAL ISA files.

    Offline / reuse path: if ISA-Tab files (i_*.txt, s_*.txt, a_*.txt,
    m_*.tsv) are already on disk, parse them without any network access —
    the same parsers used by :func:`inspect_studies`.

    Parameters
    ----------
    study_id : str
        MetaboLights accession (e.g. ``"MTBLS1375"``).
    isa_dir : str | Path
        Directory containing the ISA metadata files.

    Returns
    -------
    StudyCandidate
        Populated as if deep-inspected.
    """
    base = StudyCandidate(study_id=study_id)
    study_path = Path(isa_dir)
    if not study_path.is_dir():
        raise NotADirectoryError(f"{study_path} is not a directory")

    enrichment = {
        "investigation_file_parsed": False,
        "assays": [],
        "assay_files_parsed": False,
        "sample_file_parsed": False,
        "maf_files_parsed": False,
        "protocols": [],
        "publications": [],
        "metabolite_count": None,
        "sample_metadata_fields": [],
        "sample_metadata": [],
        "data_files": [],
        "sample_file_map": {},
    }

    enrichment.update(_parse_investigation(study_id, study_path))
    _parse_assay_files(enrichment, study_id, study_path)
    _parse_sample_file(enrichment, study_id, study_path)
    _parse_maf_files(enrichment, study_id, study_path)
    _merge_enriched(base, enrichment)
    return base


def _inspect_one(
    study_id: str, tmp_dir: str | None, download_data_files: bool
) -> dict[str, Any]:
    """Inspect a single study and return enrichment data as a dict."""
    if tmp_dir:
        study_tmp = Path(tmp_dir) / study_id
    else:
        study_tmp = Path(tempfile.mkdtemp(prefix=f"{study_id}_"))

    study_tmp.mkdir(parents=True, exist_ok=True)
    study_path = study_tmp / study_id  # HTTP downloads go here

    try:
        _download_isa_files(study_id, str(study_tmp))
        enrichment = _parse_investigation(study_id, study_path)
        _parse_assay_files(enrichment, study_id, study_path)
        _parse_sample_file(enrichment, study_id, study_path)
        _parse_maf_files(enrichment, study_id, study_path)
        if download_data_files:
            _list_data_files(enrichment, study_id, study_path)
        return enrichment
    finally:
        if not tmp_dir:
            import shutil
            shutil.rmtree(study_tmp, ignore_errors=True)


def _download_isa_files(study_id: str, dest: str) -> None:
    """Download ISA metadata files via HTTP (much faster than FTP).

    Files are saved to ``{dest}/{study_id}/``.
    """
    study_url = f"{HTTP_STUDY_BASE}/{study_id}/"
    target_dir = Path(dest) / study_id
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: Get directory listing via HTTP to find ISA file names
        resp = _http_get_with_retry(study_url, timeout=30)
        resp.raise_for_status()

        isa_files = re.findall(
            r'href="([^"]+(?:\.txt|\.tsv))"', resp.text
        )
        isa_files = [
            f for f in isa_files
            if f.startswith(("i_", "s_", "a_", "m_")) and not f.startswith(".")
        ]
        if not isa_files:
            raise RuntimeError(f"No ISA files found for {study_id}")

        # Step 2: Download all ISA files in parallel
        def _dl(name: str) -> tuple[str, int]:
            r = _http_get_with_retry(study_url + name, timeout=60)
            r.raise_for_status()
            (target_dir / name).write_bytes(r.content)
            return name, len(r.content)

        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(_dl, f): f for f in isa_files}
            for fut in as_completed(futs):
                fut.result()  # re-raise if any failed
        logger.debug("Downloaded %d ISA files for %s via HTTP", len(isa_files), study_id)
    except Exception as e:
        logger.debug("HTTP download for %s failed (%s), trying REST fallback ...", study_id, e)
        _download_via_rest(study_id, dest)


def _http_get_with_retry(url: str, timeout: int = 30, retries: int = 3) -> httpx.Response:
    """GET with retry on transient SSL/timeout errors."""
    import time as _time

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            return httpx.get(url, timeout=timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            last_err = e
            wait = 2 * (attempt + 1)
            logger.debug("Retry %d/%d for %s after %.0fs",
                         attempt + 1, retries, url, wait)
            _time.sleep(wait)
    raise last_err or RuntimeError(f"Failed after {retries} retries: {url}")


def _download_via_rest(study_id: str, dest: str) -> None:
    """Fallback: download ISA metadata as ZIP via MetaboLights REST API."""
    url = f"https://www.ebi.ac.uk/metabolights/ws/studies/{study_id}/download/isa"
    target_dir = Path(dest) / study_id
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        resp = httpx.get(url, params={"format": "zip"}, timeout=60)
        resp.raise_for_status()
        import zipfile, io
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(target_dir)
        logger.debug("REST download succeeded for %s", study_id)
    except Exception as e:
        logger.warning("REST download for %s failed: %s", study_id, e)
        raise


def _parse_investigation(study_id: str, study_path: Path) -> dict[str, Any]:
    """Parse the investigation file and return enrichment data."""
    enrichment: dict[str, Any] = {
        "investigation_file_parsed": False,
        "assays": [],
        "assay_files_parsed": False,
        "sample_file_parsed": False,
        "maf_files_parsed": False,
        "protocols": [],
        "publications": [],
        "metabolite_count": None,
        "sample_metadata_fields": [],
        "sample_metadata": [],
        "data_files": [],
        "sample_file_map": {},
    }

    # Find investigation file
    inv_file = None
    for f in study_path.iterdir():
        if RE_INVESTIGATION.match(f.name):
            inv_file = f
            break

    if inv_file is None:
        logger.debug("No investigation file found for %s", study_id)
        return enrichment

    try:
        model, messages = parse_investigation_from_fs(str(inv_file))
        if model is None:
            return enrichment

        enrichment["investigation_file_parsed"] = True

        # Extract protocols (they live in .protocols attribute)
        protocol_list = []
        for study in model.studies or []:
            proto_container = study.study_protocols
            if proto_container and proto_container.protocols:
                for proto in proto_container.protocols:
                    protocol_list.append(
                        ProtocolInfo(
                            name=proto.name or "",
                            description=proto.description or "",
                            protocol_type=proto.protocol_type.term if proto.protocol_type else "",
                        )
                    )
        enrichment["protocols"] = protocol_list

        # Extract publications
        pub_list = []
        for study in model.studies or []:
            pub_container = study.study_publications
            if pub_container and pub_container.publications:
                for pub in pub_container.publications:
                    pub_list.append({
                        "doi": pub.doi or "",
                        "pubmed_id": pub.pub_med_id or "",
                        "title": pub.title or "",
                        "author_list": pub.author_list or "",
                        "status": pub.status.term if pub.status else "",
                    })
        enrichment["publications"] = pub_list

    except Exception as e:
        logger.debug("Failed to parse investigation for %s: %s", study_id, e)

    return enrichment


def _parse_assay_files(enrichment: dict, study_id: str, study_path: Path) -> None:
    """Parse assay files and populate assay info."""
    assay_files = [f for f in study_path.iterdir() if RE_ASSAY.match(f.name)]
    if not assay_files:
        return

    assays = []
    sample_file_map: dict[str, dict[str, list[str]]] = {}
    for af in assay_files:
        try:
            isa_file, messages = parse_isa_table_sheet_from_fs(str(af))
            if isa_file is None or isa_file.table is None:
                continue
            table = isa_file.table

            technique_name = ""
            technique_main = ""
            technique_sub = ""
            instrument = ""
            column_type = ""
            ionization_mode = ""
            raw_count = 0

            # Map sample_name -> raw/derived data files from assay columns
            sample_col = _find_column(table, "sample name")
            raw_col = _find_column(table, "raw spectral data file")
            derived_col = _find_column(table, "derived spectral data file")
            if sample_col and (raw_col or derived_col):
                sample_vals = table.data.get(sample_col, []) or []
                raw_vals = table.data.get(raw_col, []) or [] if raw_col else []
                derived_vals = table.data.get(derived_col, []) or [] if derived_col else []
                for i, sname in enumerate(sample_vals):
                    sname = (sname or "").strip()
                    if not sname:
                        continue
                    entry = sample_file_map.setdefault(sname, {"raw": [], "derived": []})
                    if i < len(raw_vals) and raw_vals[i] and raw_vals[i].strip():
                        entry["raw"].append(raw_vals[i].strip())
                    if i < len(derived_vals) and derived_vals[i] and derived_vals[i].strip():
                        entry["derived"].append(derived_vals[i].strip())

            # Extract info from columns
            if table.columns:
                for col in table.columns:
                    col_lower = col.lower()
                    if "instrument" in col_lower:
                        vals = table.data.get(col, []) or []
                        for v in vals:
                            if v and v.strip():
                                instrument = v.strip()
                                break
                    if "column" in col_lower:
                        vals = table.data.get(col, []) or []
                        for v in vals:
                            if v and v.strip():
                                column_type = v.strip()
                                break
                    # Priority: scan polarity > ion mode > ion source
                    if any(kw in col_lower for kw in ["parameter value[scan polarity", "scan polarity", "parameter value[ion mode", "ion mode"]):
                        vals = table.data.get(col, []) or []
                        for v in vals:
                            if v and v.strip():
                                ionization_mode = v.strip()
                                break
                    elif "parameter value[ion source" in col_lower or "ion source" in col_lower:
                        vals = table.data.get(col, []) or []
                        for v in vals:
                            if v and v.strip():
                                if not ionization_mode:  # only if not already set
                                    ionization_mode = v.strip()
                                break

                # Determine technique from file name
                fname = af.name.lower()
                if "nmr" in fname:
                    technique_name = "NMR"
                    technique_main = "NMR"
                elif "lc" in fname:
                    technique_name = "LC-MS"
                    technique_main = "MS"
                    technique_sub = "LC"
                elif "gc" in fname:
                    technique_name = "GC-MS"
                    technique_main = "MS"
                    technique_sub = "GC"
                elif "ms" in fname:
                    technique_name = "MS"
                    technique_main = "MS"

            # Count raw/derived files
            for col in table.columns or []:
                if "raw" in col.lower() or "spectrum" in col.lower():
                    vals = table.data.get(col, []) or []
                    raw_count = sum(1 for v in vals if v and v.strip())

            assays.append(
                AssayInfo(
                    technique_name=technique_name,
                    technique_main=technique_main,
                    technique_sub=technique_sub,
                    instrument=instrument,
                    column_type=column_type,
                    ionization_mode=ionization_mode,
                    raw_file_count=raw_count,
                )
            )
        except Exception as e:
            logger.debug("Failed to parse assay file %s: %s", af.name, e)

    enrichment["assays"] = assays
    enrichment["assay_files_parsed"] = True
    if sample_file_map:
        enrichment["sample_file_map"] = sample_file_map


def _find_column(table, keyword: str) -> str:
    """Find a column name in the table matching the keyword (case-insensitive)."""
    for col in table.columns or []:
        if keyword in col.lower():
            return col
    return ""

def _parse_sample_file(enrichment: dict, study_id: str, study_path: Path) -> None:
    """Parse the sample file to extract sample metadata fields."""
    sample_files = [f for f in study_path.iterdir() if RE_SAMPLES.match(f.name)]
    if not sample_files:
        return

    sf = sample_files[0]
    try:
        isa_file, messages = parse_isa_table_sheet_from_fs(str(sf))
        if isa_file is None or isa_file.table is None:
            return
        table = isa_file.table

        enrichment["sample_metadata_fields"] = table.columns or []

        if table.data and table.columns:
            first_col = table.columns[0]
            values = table.data.get(first_col, []) or []
            samples = [v for v in values if v and v.strip()]
            if len(samples) > 1:
                sample_meta = []
                for i in range(len(samples)):
                    row = {}
                    for col in table.columns or []:
                        vals = table.data.get(col, []) or []
                        if i < len(vals):
                            row[col] = vals[i] or ""
                    sample_meta.append(row)
                enrichment["sample_metadata"] = sample_meta

        enrichment["sample_file_parsed"] = True
    except Exception as e:
        logger.debug("Failed to parse sample file %s: %s", sf.name, e)


def _parse_maf_files(enrichment: dict, study_id: str, study_path: Path) -> None:
    """Parse metabolite assignment files (MAF) for metabolite count."""
    maf_files = [f for f in study_path.iterdir() if RE_MAF.match(f.name)]
    if not maf_files:
        return

    total_metabolites = 0
    for mf in maf_files:
        try:
            isa_file, messages = parse_isa_table_sheet_from_fs(str(mf))
            if isa_file is None or isa_file.table is None:
                continue
            table = isa_file.table
            if table.data and table.columns:
                first_col = table.columns[0]
                vals = table.data.get(first_col, []) or []
                total_metabolites += sum(1 for v in vals if v and v.strip())
        except Exception as e:
            logger.debug("Failed to parse MAF file %s: %s", mf.name, e)

    if total_metabolites > 0:
        enrichment["metabolite_count"] = total_metabolites
        enrichment["maf_files_parsed"] = True


def _list_data_files(enrichment: dict, study_id: str, study_path: Path) -> None:
    """List data files from the FILES directory if it was downloaded."""
    files_dir = study_path / "FILES"
    if not files_dir.exists():
        return

    data_files = []
    for f in files_dir.rglob("*"):
        if f.is_file():
            suffix = f.suffix.lower()
            data_files.append(
                DataFileInfo(
                    path=str(f.relative_to(study_path)),
                    size_bytes=f.stat().st_size,
                    format=suffix,
                )
            )

    enrichment["data_files"] = data_files


def _merge_enriched(base: StudyCandidate, enrichment: dict[str, Any]) -> None:
    """Merge enrichment data back into the original candidate."""
    if enrichment.get("investigation_file_parsed"):
        base.investigation_file_parsed = True

    if enrichment.get("assay_files_parsed"):
        base.assay_files_parsed = True
        if enrichment.get("assays"):
            base.assays = enrichment["assays"]
        if enrichment.get("sample_file_map"):
            base.sample_file_map = enrichment["sample_file_map"]

    if enrichment.get("sample_file_parsed"):
        base.sample_file_parsed = True
        if enrichment.get("sample_metadata_fields"):
            base.sample_metadata_fields = enrichment["sample_metadata_fields"]
        if enrichment.get("sample_metadata"):
            base.sample_metadata = enrichment["sample_metadata"]
            # Update sample_count from actual parsed data
            base.sample_count = len(enrichment["sample_metadata"])

    if enrichment.get("maf_files_parsed"):
        base.maf_files_parsed = True
        base.metabolite_count = enrichment.get("metabolite_count")

    if enrichment.get("protocols"):
        base.protocols = enrichment["protocols"]

    if enrichment.get("publications"):
        # Only overwrite if we got deeper info
        existing_dois = {p.doi for p in base.publications}
        for pub_data in enrichment["publications"]:
            doi_val = pub_data.get("doi", "") if isinstance(pub_data, dict) else getattr(pub_data, "doi", "")
            if doi_val and doi_val not in existing_dois:
                from mtbls_agent.models import PublicationInfo
                if isinstance(pub_data, dict):
                    base.publications.append(PublicationInfo(**pub_data))
                else:
                    base.publications.append(pub_data)

    if enrichment.get("investigation_file_parsed"):
        base.investigation_file_parsed = True

    # Calculate metadata completeness
    _compute_completeness(base)


def _compute_completeness(candidate: StudyCandidate) -> None:
    """Compute a 0-1 metadata completeness score."""
    checks = [
        bool(candidate.title),
        bool(candidate.description),
        candidate.sample_count is not None and candidate.sample_count > 0,
        candidate.investigation_file_parsed,
        candidate.assay_files_parsed,
        candidate.sample_file_parsed,
        candidate.maf_files_parsed,
        bool(candidate.publications),
        bool(candidate.protocols),
        candidate.sample_count is not None and candidate.sample_count > 0,
        candidate.raw_file_count is not None and candidate.raw_file_count > 0,
        bool(candidate.organisms),
        bool(candidate.assay_techniques),
    ]
    candidate.metadata_completeness = sum(checks) / len(checks) if checks else 0.0