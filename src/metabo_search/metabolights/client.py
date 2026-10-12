"""Client for the MetaboLights public study index.

This is the search half of the MetaboLights client: free-text and facet search
over the public study index, plus point lookups by accession. Deep inspection
(ISA + MAF) lives in a separate module.

Every method is offline-testable via an injected ``httpx`` transport.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import TracebackType
from typing import Literal

import httpx

from metabo_search.errors import APIError, NotFound
from metabo_search.metabolights.files import download as _download
from metabo_search.metabolights.files import isa_files, list_files
from metabo_search.metabolights.models import (
    DownloadReport,
    SearchPage,
    SearchResponse,
    StudyDetail,
    StudyFile,
    StudyHit,
)
from metabo_search.metabolights.parse import parse_study_detail
from metabo_search.results import Results
from metabo_search.transport import HttpClient, HttpRecord, RateLimit, RetryPolicy

BASE_URL = "https://www.ebi.ac.uk/metabolights/ws3"
SEARCH_PATH = "public/v2/public-study-index/search"
MAX_PAGE_SIZE = 100

SortDirection = Literal["asc", "desc"]


class MetaboLights:
    """Search the MetaboLights public study index.

    Args:
        base_url: API base URL. Defaults to the EMBL-EBI production host.
        timeout: Read timeout in seconds.
        retry: Retry policy for transient failures.
        rate_limit: Optional token-bucket rate limit.
        headers: Extra headers merged into every request.
        raw_sink: Optional per-attempt callback for provenance.
        transport: Optional httpx transport, used by tests.
    """

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        retry: RetryPolicy | None = None,
        rate_limit: RateLimit | None = None,
        headers: Mapping[str, str] | None = None,
        raw_sink: Callable[[HttpRecord], None] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = HttpClient(
            base_url,
            timeout=timeout,
            retry=retry,
            rate_limit=rate_limit,
            headers=headers,
            raw_sink=raw_sink,
            transport=transport,
        )

    def search(
        self,
        query: str = "",
        *,
        organisms: Sequence[str] | None = None,
        sample_types: Sequence[str] | None = None,
        techniques: Sequence[str] | None = None,
        design_descriptors: Sequence[str] | None = None,
        min_samples: int | None = None,
        min_raw_files: int | None = None,
        sort: str | None = None,
        sort_direction: SortDirection = "desc",
        limit: int = 25,
    ) -> Results[StudyHit]:
        """Search the public study index.

        Facet filters are applied server-side; ``min_samples`` and
        ``min_raw_files`` are applied client-side (the search API has no
        numeric filter), so more pages may be fetched to fill ``limit``.

        Args:
            query: Free-text query.
            organisms: Organism terms (the ``organisms.term`` facet).
            sample_types: Sample types (the ``organismParts.term`` facet).
            techniques: Assay techniques, e.g. ``"LC-MS"``.
            design_descriptors: Design-descriptor terms.
            min_samples: Keep studies with at least this many samples.
            min_raw_files: Keep studies with at least this many raw files.
            sort: Sort field, e.g. ``"sampleCount"`` or ``"submissionDate"``.
            sort_direction: Sort direction.
            limit: Maximum number of hits to return.

        Returns:
            The matching study documents, in API order.

        Raises:
            APIError: The API returned an error envelope.
        """
        assert limit >= 1, "limit must be >= 1"
        filters = _build_filters(organisms, sample_types, techniques, design_descriptors)
        out: Results[StudyHit] = Results()
        seen: set[str] = set()
        for hit in self._iter_hits(
            query, filters, page_size=MAX_PAGE_SIZE, sort=sort, sort_direction=sort_direction
        ):
            if hit.study_id in seen:
                continue
            seen.add(hit.study_id)
            if min_samples is not None and (hit.sample_count or 0) < min_samples:
                continue
            if min_raw_files is not None and (hit.raw_file_count or 0) < min_raw_files:
                continue
            out.append(hit)
            if len(out) >= limit:
                break
        return out

    def study(self, study_id: str) -> StudyHit:
        """Fetch one study document by accession.

        Args:
            study_id: MetaboLights accession, e.g. ``"MTBLS1"``.

        Returns:
            The study document.

        Raises:
            NotFound: No study with that accession exists.
            APIError: The API returned an error envelope.
        """
        assert study_id, "study_id must be non-empty"
        hits = self.studies([study_id])
        if not hits:
            raise NotFound(f"study {study_id!r} not found")
        return hits[0]

    def studies(self, study_ids: Sequence[str]) -> Results[StudyHit]:
        """Fetch several study documents by accession.

        Args:
            study_ids: Accessions to fetch. Duplicates and blanks are ignored.

        Returns:
            The found study documents in input order; unknown ids are omitted.

        Raises:
            APIError: The API returned an error envelope.
        """
        ids = list(dict.fromkeys(sid for sid in study_ids if sid))
        if not ids:
            return Results()
        filters: list[dict[str, object]] = [
            {"field": "studyId", "values": ids, "operator": "any"}
        ]
        found: dict[str, StudyHit] = {}
        for hit in self._iter_hits(
            "", filters, page_size=min(MAX_PAGE_SIZE, len(ids)), sort=None
        ):
            found.setdefault(hit.study_id, hit)
            if len(found) >= len(ids):
                break
        return Results(found[sid] for sid in ids if sid in found)

    def files(self, study_id: str, *, recursive: bool = True) -> Results[StudyFile]:
        """List a study's data files from its ``FILES/`` tree.

        Args:
            study_id: MetaboLights accession.
            recursive: Descend into subdirectories (default) or list only the
                top level of ``FILES/``.

        Returns:
            The files found, in listing order.
        """
        return list_files(self._http, study_id, recursive=recursive)

    def download(
        self,
        study_id: str,
        selection: str | Sequence[StudyFile] = "all",
        *,
        dest: str | Path = ".",
        max_files: int | None = None,
        max_bytes: int | None = None,
        workers: int = 4,
    ) -> DownloadReport:
        """Download a selection of a study's files.

        Args:
            study_id: MetaboLights accession.
            selection: ``"all"``, a category (``"raw"``/``"derived"``/
                ``"other"``), or an explicit sequence of :class:`StudyFile`.
            dest: Root destination; files land under ``dest/study_id/``.
            max_files: Keep at most this many files.
            max_bytes: Stop before the cumulative listed size exceeds this.
            workers: Parallel download threads.

        Returns:
            A report with the files downloaded, failures, and total bytes.
        """
        return _download(
            self._http,
            study_id,
            selection,
            dest=dest,
            max_files=max_files,
            max_bytes=max_bytes,
            workers=workers,
        )

    def inspect(
        self,
        study_ids: Sequence[str | StudyHit],
        *,
        workers: int = 8,
        include_files: bool = False,
    ) -> Results[StudyDetail]:
        """Download and parse deep per-study data (ISA samples + MAF metabolites).

        Args:
            study_ids: Accessions, or study documents to read accessions from.
            workers: Parallel study downloads.
            include_files: Also list each study's ``FILES/`` tree (slower).

        Returns:
            One :class:`StudyDetail` per input, in input order.
        """
        ids = [item if isinstance(item, str) else item.study_id for item in study_ids]
        if not ids:
            return Results()

        def _run(study_id: str) -> StudyDetail:
            return self._inspect_one(study_id, include_files)

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            details = list(pool.map(_run, ids))
        return Results(details)

    def _inspect_one(self, study_id: str, include_files: bool) -> StudyDetail:
        isa = isa_files(self._http, study_id)
        files: list[StudyFile] = list(self.files(study_id)) if include_files else []
        return parse_study_detail(study_id, isa, files=files)

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._http.close()

    def __enter__(self) -> MetaboLights:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- internals -------------------------------------------------------

    def _iter_hits(
        self,
        query: str,
        filters: list[dict[str, object]],
        *,
        page_size: int,
        sort: str | None,
        sort_direction: SortDirection = "desc",
    ) -> Iterator[StudyHit]:
        """Yield every hit across pages until the index is exhausted."""
        page = 1
        while True:
            body = _search_body(query, filters, page, page_size, sort, sort_direction)
            result = self._post_search(body)
            yield from result.results
            if not result.results or page * page_size >= result.total_results:
                return
            page += 1

    def _post_search(self, body: dict[str, object]) -> SearchPage:
        response = self._http.post_json(SEARCH_PATH, model=SearchResponse, json=body)
        if response.status != "success":
            raise APIError(
                response.error_message or "MetaboLights search failed",
                error_message=response.error_message,
                errors=response.errors,
            )
        assert response.content is not None, "successful response without content"
        return response.content


def _build_filters(
    organisms: Sequence[str] | None,
    sample_types: Sequence[str] | None,
    techniques: Sequence[str] | None,
    design_descriptors: Sequence[str] | None,
) -> list[dict[str, object]]:
    """Map shortcut arguments to search-index filter objects."""
    filters: list[dict[str, object]] = []
    for field, values in (
        ("organisms.term", organisms),
        ("organismParts.term", sample_types),
        ("assayTechniques.name", techniques),
        ("designDescriptors.term", design_descriptors),
    ):
        if values:
            filters.append({"field": field, "values": list(values), "operator": "any"})
    return filters


def _search_body(
    query: str,
    filters: list[dict[str, object]],
    page: int,
    page_size: int,
    sort: str | None,
    sort_direction: SortDirection,
) -> dict[str, object]:
    """Build the search request body."""
    body: dict[str, object] = {"page": {"current": page, "size": page_size}}
    if query:
        body["query"] = query
    if filters:
        body["filters"] = filters
    if sort:
        body["sort"] = {"field": sort, "direction": sort_direction}
    return body
