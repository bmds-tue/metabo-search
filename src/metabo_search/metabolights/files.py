"""Recursive ``FILES/`` listing and selective download for MetaboLights.

MetaboLights serves study data from an HTTP mirror of its FTP tree. Directory
listings are HTML; this module parses them, categorizes files (raw vs derived,
directory hints beat extension), and downloads a selection.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from metabo_search.errors import MetaboSearchError
from metabo_search.metabolights.models import DownloadReport, StudyFile
from metabo_search.results import Results
from metabo_search.transport import HttpClient

MIRROR_BASE = "https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public"
MAX_DEPTH = 6

RAW_EXTS = frozenset({".raw", ".d", ".wiff", ".wiff2", ".baf", ".qgd", ".qgb", ".fid"})
RAW_COMPRESSED = frozenset({".d.zip", ".raw.zip", ".wiff.zip"})
DERIVED_EXTS = frozenset(
    {
        ".mzml",
        ".mzxml",
        ".nmrml",
        ".mzdata",
        ".cdf",
        ".imzml",
        ".mz5",
        ".mgf",
        ".apex",
        ".ft2",
        ".ms2",
        ".dta",
    }
)

_LINKS = re.compile(r'href="([^"]+)"[^>]*>([^<]*)</a>')
_SIZE_AFTER = re.compile(
    r"</a></td><td[^>]*>[^<]*</td><td[^>]*>\s*([\d.]+\s*[KMGTPE]?B?)\s*</td>",
    re.IGNORECASE,
)
_SIZE_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5, "E": 1024**6}

_ISA_NAME = re.compile(r"^[isam]_[^/]+\.(txt|tsv)$", re.IGNORECASE)


def parse_size(text: str) -> int:
    """Parse a human-readable size like ``"2.1M"`` into bytes.

    Args:
        text: A size string as shown in an Apache directory listing.

    Returns:
        The size in bytes, or ``0`` when it cannot be parsed.
    """
    value = text.strip().upper().removesuffix("B")
    if not value:
        return 0
    suffix = value[-1]
    if suffix in _SIZE_UNITS:
        number = value[:-1].strip()
        if not number:
            return 0
        try:
            return int(float(number) * _SIZE_UNITS[suffix])
        except ValueError:
            return 0
    try:
        return int(float(value))
    except ValueError:
        return 0


def detect_extension(filename: str) -> str:
    """Return the file extension, keeping compound suffixes like ``.d.zip``."""
    suffixes = Path(filename).suffixes
    if len(suffixes) >= 2:
        compound = (suffixes[-2] + suffixes[-1]).lower()
        if compound in RAW_COMPRESSED:
            return compound
    return suffixes[-1].lower() if suffixes else ""


def categorize(extension: str, relative_path: str = "") -> str:
    """Classify a file as ``"raw"``, ``"derived"``, or ``"other"``.

    Directory hints win over the extension: an ``.mzML`` under ``RAW_FILES/``
    is instrument output, so it is raw.
    """
    # Match directory *segments*, not substrings: "FILES/DRAWINGS/x" must not
    # be treated as raw just because "DRAWINGS" contains "RAW".
    segments = relative_path.upper().split("/")
    if any(segment.startswith(("DERIVED", "PROCESSED")) for segment in segments):
        return "derived"
    if any(segment.startswith("RAW") for segment in segments):
        return "raw"
    if extension in RAW_EXTS or extension in RAW_COMPRESSED:
        return "raw"
    if extension in DERIVED_EXTS:
        return "derived"
    return "other"


def isa_files(http: HttpClient, study_id: str) -> dict[str, str]:
    """Download a study's ISA-Tab files as ``{file_name: text}``.

    Lists the study root (not ``FILES/``) and fetches every ``i_*.txt``,
    ``s_*.txt``, ``a_*.txt``, and ``m_*.tsv``.

    Args:
        http: The transport to use.
        study_id: MetaboLights accession.

    Returns:
        File name to file contents, in listing order.
    """
    assert study_id, "study_id must be non-empty"
    html = http.get_text(f"{MIRROR_BASE}/{study_id}/")
    names = [link for link, _label in _LINKS.findall(html) if _ISA_NAME.match(link)]
    return {name: http.get_text(f"{MIRROR_BASE}/{study_id}/{name}") for name in names}


def list_files(
    http: HttpClient, study_id: str, *, recursive: bool = True
) -> Results[StudyFile]:
    """List a study's data files by walking its ``FILES/`` tree.

    Args:
        http: The transport to use.
        study_id: MetaboLights accession.
        recursive: Descend into subdirectories (default) or list only the top
            level of ``FILES/``.

    Returns:
        The files found, in listing order.
    """
    assert study_id, "study_id must be non-empty"
    files: Results[StudyFile] = Results()
    _walk(http, f"{MIRROR_BASE}/{study_id}/FILES/", "FILES", files, 0, recursive)
    return files


def _walk(
    http: HttpClient,
    url: str,
    prefix: str,
    files: Results[StudyFile],
    depth: int,
    recursive: bool,
) -> None:
    """Recursively walk an HTTP directory listing, appending to ``files``."""
    if depth > MAX_DEPTH:
        return
    try:
        html = http.get_text(url)
    except MetaboSearchError:
        return  # a missing or unreachable subdirectory is skipped, not fatal
    for link, label in _LINKS.findall(html):
        if not link or link.startswith(("?", ".", "/", "http")) or ".." in link:
            continue
        if "Parent Directory" in label:
            continue
        if link.endswith("/"):
            if recursive:
                sub = f"{prefix}/{link.rstrip('/')}"
                _walk(http, url + link, sub, files, depth + 1, recursive)
            continue
        extension = detect_extension(link)
        path = f"{prefix}/{link}"
        files.append(
            StudyFile(
                path=path,
                name=link,
                directory=prefix,
                extension=extension,
                size_bytes=_size_after(html, link),
                category=categorize(extension, path),
            )
        )


def _size_after(html: str, link: str) -> int:
    """Extract a file's size from its row in an Apache directory listing."""
    index = html.find(f'href="{link}"')
    if index < 0:
        return 0
    match = _SIZE_AFTER.search(html[index : index + 500])
    return parse_size(match.group(1)) if match else 0


def download(
    http: HttpClient,
    study_id: str,
    selection: str | Sequence[StudyFile],
    *,
    dest: str | Path = ".",
    max_files: int | None = None,
    max_bytes: int | None = None,
    workers: int = 4,
) -> DownloadReport:
    """Download a selection of a study's files into ``dest/study_id``.

    Args:
        http: The transport to use.
        study_id: MetaboLights accession.
        selection: ``"all"``, a category (``"raw"``/``"derived"``/``"other"``),
            or an explicit sequence of :class:`StudyFile`.
        dest: Root destination directory; files land under ``dest/study_id/``
            preserving their ``FILES/`` relative path.
        max_files: Keep at most this many files (after selection).
        max_bytes: Stop before the cumulative listed size exceeds this.
        workers: Parallel download threads.

    Returns:
        A :class:`DownloadReport` with the files downloaded, failures, and the
        total bytes written.
    """
    files: Sequence[StudyFile] = _select(http, study_id, selection)
    if max_files is not None:
        files = files[:max_files]
    if max_bytes is not None:
        files = _cap_bytes(files, max_bytes)

    dest_dir = Path(dest) / study_id
    downloaded: list[StudyFile] = []
    failed: list[str] = []
    total = 0

    def _one(file: StudyFile) -> tuple[StudyFile, int, str | None]:
        try:
            written = http.download(f"{MIRROR_BASE}/{study_id}/{file.path}", dest_dir / file.path)
            return file, written, None
        except Exception as exc:  # noqa: BLE001 — report, don't abort the batch
            return file, 0, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for file, written, error in pool.map(_one, files):
            if error is None:
                downloaded.append(file)
                total += written
            else:
                failed.append(file.path)

    return DownloadReport(
        dest_dir=str(dest_dir),
        downloaded=downloaded,
        failed=failed,
        total_bytes=total,
    )


def _select(
    http: HttpClient, study_id: str, selection: str | Sequence[StudyFile]
) -> Results[StudyFile]:
    """Resolve a selection to a concrete list of files."""
    if not isinstance(selection, str):
        return Results(selection)
    files = list_files(http, study_id)
    if selection == "all":
        return files
    if selection in ("raw", "derived", "other"):
        return Results(file for file in files if file.category == selection)
    raise ValueError(f"unknown selection {selection!r}")


def _cap_bytes(files: Sequence[StudyFile], max_bytes: int) -> Results[StudyFile]:
    """Keep files until their cumulative listed size would exceed ``max_bytes``."""
    out: Results[StudyFile] = Results()
    total = 0
    for file in files:
        if total + file.size_bytes > max_bytes:
            break
        out.append(file)
        total += file.size_bytes
    return out
