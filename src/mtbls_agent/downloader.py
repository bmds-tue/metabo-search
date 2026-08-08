"""Selective data file download from MetaboLights studies.

Supports fine-grained filtering by file type, sample name, raw vs derived.
"""

from __future__ import annotations

import logging
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
import threading

import httpx

from mtbls_agent.models import StudyCandidate

logger = logging.getLogger(__name__)

HTTP_STUDY_BASE = "https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public"

RAW_EXTS = {".raw", ".d", ".wiff", ".wiff2", ".baf", ".qgd", ".qgb", ".fid"}
RAW_COMPRESSED = {".d.zip", ".raw.zip", ".wiff.zip"}
DERIVED_EXTS = {".mzml", ".mzxml", ".mzdata", ".cdf", ".imzml", ".mz5",
                ".mgf", ".apex", ".ft2", ".ms2", ".dta"}


@dataclass
class DataFileRef:
    relative_path: str = ""
    size_bytes: int = 0
    file_type: str = ""
    category: str = ""
    assay_name: str = ""
    sample_name: str = ""


@dataclass
class DownloadConfig:
    file_types: list[str] | None = None
    sample_names: list[str] | None = None
    categories: list[str] | None = None
    dest_dir: str | None = None
    max_files: int | None = None
    max_size_gb: float | None = None
    parallel_downloads: int = 4


@dataclass
class DownloadResult:
    downloaded: list[DataFileRef] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    total_bytes: int = 0
    dest_dir: str = ""
    """Local directory where files were saved."""


class DownloadTask:
    def __init__(self):
        self._future = None

    def result(self, timeout=None):
        if self._future is None:
            raise RuntimeError("Download not started")
        return self._future.result(timeout=timeout)

    @property
    def done(self):
        return self._future is not None and self._future.done()


def start_download(candidate, config=None):
    task = DownloadTask()
    pool = ThreadPoolExecutor(max_workers=1)
    def _run():
        return download_data_files(candidate, config)
    task._future = pool.submit(_run)
    pool.shutdown(wait=False)
    return task


def _parse_size(size_str: str) -> int:
    """Parse human-readable size like '2.1M' or '14M' into bytes."""
    size_str = size_str.strip().upper()
    if not size_str:
        return 0
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    suffix = size_str[-1]
    if suffix in units:
        try:
            num = size_str[:-1].rstrip("B")
            return int(float(num) * units[suffix])
        except ValueError:
            return 0
    try:
        return int(float(size_str.rstrip("B")))
    except ValueError:
        return 0


def _infer_sample_name(filename: str) -> str:
    """Guess the sample name from a data filename (strip extensions)."""
    p = Path(filename)
    stem = p.stem
    if p.suffix == ".zip" and stem.endswith(".d"):
        stem = stem[:-2]
    return stem


def _detect_ext(filename: str) -> str:
    p = Path(filename)
    suffixes = p.suffixes
    if len(suffixes) >= 2:
        compound = suffixes[-2] + suffixes[-1]
        if compound.lower() in RAW_COMPRESSED:
            return compound.lower()
    return suffixes[-1].lower() if suffixes else ""


def _categorize(ext: str, rel_path: str = "") -> str:
    """Raw/derived/other by extension, overridden by the directory name.

    An mzML under FILES/RAW_FILES/ is instrument output -> raw, even though
    .mzml normally means derived.  Strong directory hints beat extension.
    """
    up = rel_path.upper()
    if "DERIVED" in up or "PROCESSED" in up:
        return "derived"
    if "RAW" in up:
        return "raw"
    if ext in RAW_EXTS or ext in RAW_COMPRESSED:
        return "raw"
    if ext in DERIVED_EXTS:
        return "derived"
    return "other"


def list_data_files(candidate: StudyCandidate) -> list[DataFileRef]:
    """List data files recursively through FILES/ and its subdirectories.

    Some studies organize data in FILES/RAW_FILES/, FILES/DERIVED_FILES/, etc.
    Walks the HTTP directory tree, parsing filenames + sizes from HTML tables.
    """
    files: list[DataFileRef] = []
    try:
        _walk_dir(
            url=f"{HTTP_STUDY_BASE}/{candidate.study_id}/FILES/",
            rel_prefix="FILES",
            files=files,
            depth=0,
        )
    except Exception as e:
        logger.debug("HTTP listing failed for %s: %s", candidate.study_id, e)
        return []
    return files


def _walk_dir(url: str, rel_prefix: str, files: list[DataFileRef], depth: int) -> None:
    """Recursively walk an HTTP directory listing, appending to ``files``."""
    if depth > 6:  # safety bound
        return

    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # Extract all hrefs (files and subdirectories)
    links = re.findall(r'href="([^"]+)"[^>]*>([^<]*)</a>', html)
    for link, _label in links:
        if link.startswith("?") or link.startswith("."):
            continue
        # Skip absolute URLs (e.g. Parent Directory pointing upward)
        if link.startswith("/") or link.startswith("http"):
            continue
        if "Parent Directory" in _label or ".." in link:
            continue

        if link.endswith("/"):
            # Subdirectory — recurse
            sub_prefix = f"{rel_prefix}/{link.rstrip('/')}"
            _walk_dir(url + link, sub_prefix, files, depth + 1)
            continue

        # File — extract size from the following table cells
        size_bytes = _extract_size_after(html, link)
        ext = _detect_ext(link)
        files.append(DataFileRef(
            relative_path=f"{rel_prefix}/{link}",
            size_bytes=size_bytes,
            file_type=ext,
            category=_categorize(ext, f"{rel_prefix}/{link}"),
            sample_name=_infer_sample_name(link),
        ))


def _extract_size_after(html: str, link: str) -> int:
    """Find the file size in the HTML row for a given filename."""
    # Find the position of the link, then look at the next <td> cells
    idx = html.find(f'href="{link}"')
    if idx < 0:
        return 0
    row = html[idx:idx + 500]
    # Pattern: </a></td><td>DATE</td><td>SIZE</td>
    m = re.search(r"</a></td><td[^>]*>[^<]*</td><td[^>]*>\s*([\d.]+\s*[KMGTPE]?[B]?)\s*</td>", row, re.IGNORECASE)
    if m:
        return _parse_size(m.group(1).strip())
    return 0


def format_summary(candidate: StudyCandidate) -> dict[str, int]:
    """Count data files by format, from a recursive FILES/ listing.

    A quick probe for "does this study have mzML / RAW / .d?" without
    downloading anything.  Categories reflect the directory (RAW_FILES vs
    DERIVED_FILES) when available.
    """
    counts: dict[str, int] = {}
    for f in list_data_files(candidate):
        counts[f.file_type] = counts.get(f.file_type, 0) + 1
    return counts


def _apply_filters(
    files: list[DataFileRef], config: DownloadConfig
) -> list[DataFileRef]:
    selected = list(files)
    if config.categories:
        selected = [f for f in selected if f.category in config.categories]
    if config.file_types:
        selected = [f for f in selected if f.file_type in config.file_types]
    if config.sample_names:
        selected = [f for f in selected if f.sample_name in config.sample_names]
    if config.max_files:
        selected = selected[:config.max_files]
    if config.max_size_gb:
        selected = [f for f in selected if f.size_bytes <= config.max_size_gb * 1024**3]
    return selected


def download_data_files(
    candidate: StudyCandidate,
    config: DownloadConfig | None = None,
) -> DownloadResult:
    """Download data files matching the given config filters."""
    if config is None:
        config = DownloadConfig(
            categories=None,
            file_types=[".d.zip", ".raw", ".d", ".wiff", ".wiff2"],
        )
    if config.dest_dir:
        base = Path(config.dest_dir) / candidate.study_id
    else:
        base = Path(tempfile.mkdtemp(prefix=f"{candidate.study_id}_"))
        base = base / candidate.study_id
    base.mkdir(parents=True, exist_ok=True)

    available = list_data_files(candidate)
    if not available:
        logger.warning("No data files found for %s", candidate.study_id)
        return DownloadResult(dest_dir=str(base))

    selected = _apply_filters(available, config)
    if not selected:
        logger.info("No files match the given filters")
        return DownloadResult(dest_dir=str(base))

    result = _download_files(selected, base, config.parallel_downloads, candidate.study_id)
    result.dest_dir = str(base)
    return result


def _download_files(
    files: list[DataFileRef], base_path: Path, max_workers: int = 4,
    study_id: str = "",
) -> DownloadResult:
    base_url = f"{HTTP_STUDY_BASE}/{study_id}"
    result = DownloadResult()

    def _dl_one(ref: DataFileRef) -> tuple[bool, str]:
        url = f"{base_url}/{ref.relative_path}"
        dest = base_path / ref.relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        import time as _time
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = httpx.get(url, timeout=3600)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                ref.size_bytes = len(resp.content)
                return True, ref.relative_path
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                last_err = e
                _time.sleep(2 * (attempt + 1))
            except Exception as e:
                return False, str(e)
        return False, str(last_err or "unknown error")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_dl_one, f): f for f in files}
        for fut in as_completed(futs):
            ok, msg = fut.result()
            req_file = futs[fut]
            if ok:
                result.downloaded.append(req_file)
                result.total_bytes += req_file.size_bytes
            else:
                result.failed.append(f"{req_file.relative_path}: {msg}")

    logger.info("Downloaded %d/%d data files", len(result.downloaded), len(files))
    return result
