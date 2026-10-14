"""Client for the Metabolomics Workbench REST API.

Two ways to find studies:

* :meth:`Workbench.search` ranks a locally cached whole-index summary corpus by
  title-token overlap (slow first fetch, then cached).
* :meth:`Workbench.studies_by` queries the native REST study index by one input
  item (instant, no corpus).

Both return ``Results[StudySummary]`` and feed the same :meth:`Workbench.inspect`
and :meth:`Workbench.ingest` pipeline. The native API combines nothing — no
free-text-plus-filter, and no organism or disease filter — so use the corpus
for those.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Literal, get_args
from urllib.parse import quote

import httpx

from metabo_search.errors import MetaboSearchError, NotFound, ResponseValidationError
from metabo_search.results import Results
from metabo_search.transport import (
    HttpClient,
    HttpRecord,
    HttpStats,
    RateLimit,
    RetryPolicy,
    warn_if_throttled,
)
from metabo_search.workbench.corpus import DEFAULT_CACHE_DIR, DEFAULT_TTL, CorpusCache
from metabo_search.workbench.models import (
    AnalysisRecord,
    MetaboliteRow,
    SampleRow,
    StudyDetail,
    StudySummary,
)
from metabo_search.workbench.parse import (
    normalize_rows,
    parse_metabolites,
    parse_samples,
    parse_summaries,
)

if TYPE_CHECKING:
    from metabo_search.workspace import IngestReport, Workspace

BASE_URL = "https://www.metabolomicsworkbench.org/rest"
CORPUS_TIMEOUT = 180.0

_TOKEN = re.compile(r"[a-z0-9']+")

StudyInputItem = Literal[
    "study_id",
    "analysis_id",
    "study_title",
    "institute",
    "last_name",
    "metabolite_id",
    "kegg_id",
    "refmet_name",
]
"""A native Workbench study input item: the key :meth:`Workbench.studies_by` searches on."""

_STUDY_INPUT_ITEMS: frozenset[str] = frozenset(get_args(StudyInputItem))
"""Runtime mirror of :data:`StudyInputItem`, used for the precondition check."""


class Workbench:
    """Search and inspect Metabolomics Workbench studies.

    Args:
        base_url: REST base URL.
        timeout: Read timeout in seconds. The default is generous because the
            whole-index summary corpus is slow.
        retry: Retry policy for transient failures.
        rate_limit: Optional token-bucket rate limit.
        headers: Extra headers merged into every request.
        raw_sink: Optional per-attempt callback for provenance.
        transport: Optional httpx transport, used by tests.
        cache_dir: Directory for the on-disk whole-index corpus cache, or
            ``None`` to disable it. Defaults to ``~/.cache/metabo-search``.
        corpus_ttl: How long a cached corpus stays fresh.
    """

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout: float = CORPUS_TIMEOUT,
        retry: RetryPolicy | None = None,
        rate_limit: RateLimit | None = None,
        headers: Mapping[str, str] | None = None,
        raw_sink: Callable[[HttpRecord], None] | None = None,
        transport: httpx.BaseTransport | None = None,
        cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
        corpus_ttl: timedelta = DEFAULT_TTL,
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
        self._corpus = (
            CorpusCache(Path(cache_dir), base_url, corpus_ttl)
            if cache_dir is not None
            else None
        )
        self._summaries: dict[str, StudySummary] | None = None

    def all_studies(self) -> dict[str, StudySummary]:
        """Return the whole-index study summaries.

        Resolution order: the in-memory copy, then a fresh on-disk cache (when
        a ``cache_dir`` is configured), then the API. A successful fetch is
        written back to the cache. If the fetch fails but a stale cache exists,
        the stale payload is used and a :class:`UserWarning` is emitted.

        Returns:
            Study id to summary. Cached on the instance after the first call.
        """
        if self._summaries is not None:
            return self._summaries

        cache = self._corpus
        stale: object | None = None
        if cache is not None:
            cached = cache.read()
            if cached is not None:
                payload, fetched_at = cached
                if cache.is_fresh(fetched_at):
                    self._summaries = _parse_corpus(payload)
                    return self._summaries
                stale = payload

        try:
            payload = self._get("study/study_id/ST/summary")
        except MetaboSearchError:
            if cache is None or stale is None:
                raise
            warnings.warn(
                f"using the stale Workbench corpus at {cache.path}; the refresh failed",
                stacklevel=2,
            )
            self._summaries = _parse_corpus(stale)
            return self._summaries

        if cache is not None:
            cache.write(payload)
        self._summaries = _parse_corpus(payload)
        return self._summaries

    def refresh_corpus(self) -> dict[str, StudySummary]:
        """Fetch the whole-index summaries, bypassing and rewriting the cache.

        Use this to pick up studies added upstream before the TTL expires.

        Returns:
            Study id to summary. Also replaces the in-memory copy.
        """
        payload = self._get("study/study_id/ST/summary")
        if self._corpus is not None:
            self._corpus.write(payload)
        self._summaries = _parse_corpus(payload)
        return self._summaries

    def search(
        self, query: str = "", *, min_samples: int | None = None, limit: int = 25
    ) -> Results[StudySummary]:
        """Search the whole-index summaries by title term overlap.

        This reads the cached corpus (one slow fetch, then cached). For a keyed
        lookup — a metabolite, an institute, a title — use :meth:`studies_by`,
        which is instant and never fetches the corpus.

        Args:
            query: Free-text query matched against study titles.
            min_samples: Keep studies with at least this many samples.
            limit: Maximum number of studies to return.

        Returns:
            Matching summaries, best title overlap first (ties by study id).
        """
        assert limit >= 1, "limit must be >= 1"
        tokens = _tokens(query)
        scored: list[tuple[float, StudySummary]] = []
        for summary in self.all_studies().values():
            if min_samples is not None and (summary.number_of_samples or 0) < min_samples:
                continue
            score = _overlap(tokens, _tokens(summary.study_title))
            if tokens and score == 0:
                continue
            scored.append((score, summary))
        scored.sort(key=lambda pair: (-pair[0], pair[1].study_id))
        return Results(summary for _, summary in scored[:limit])

    def study(self, study_id: str) -> StudySummary:
        """Fetch one study summary.

        A single-accession convenience over :meth:`studies_by` (``study_id``
        input, ``summary`` output).

        Args:
            study_id: Workbench accession, e.g. ``"ST000001"``.

        Returns:
            The study summary.

        Raises:
            NotFound: No study with that accession exists.
        """
        assert study_id, "study_id must be non-empty"
        summaries = self.studies_by("study_id", study_id)
        if not summaries:
            raise NotFound(f"study {study_id!r} not found")
        return summaries[0]

    def studies_by(self, input_item: StudyInputItem, value: str) -> Results[StudySummary]:
        """Find studies through the native Workbench study index.

        One input item per request; the API combines nothing. Instant, and it
        never fetches the whole-index corpus. Returns the same type as
        :meth:`search`, so both feed :meth:`inspect` and :meth:`ingest`.

        Matching is the API's own: ``study_title`` is a case-insensitive
        substring, not the token overlap :meth:`search` uses. Input items
        without a match simply return no studies. There is no ``limit``; slice
        the result if you need a cap.

        Args:
            input_item: The native search key, e.g. ``"refmet_name"``.
            value: The key value, e.g. ``"Glucose"``; URL-encoded.

        Returns:
            The matching study summaries, in API order.

        Raises:
            AssertionError: ``input_item`` is unsupported or ``value`` is empty.
        """
        assert input_item in _STUDY_INPUT_ITEMS, f"unknown study input item {input_item!r}"
        assert value, "value must be non-empty"
        payload = self._get(f"study/{input_item}/{quote(value, safe='')}/summary")
        return Results(parse_summaries(payload))

    def samples(self, study_id: str) -> Results[SampleRow]:
        """Fetch a study's per-sample factor rows."""
        return Results(parse_samples(self._get(f"study/study_id/{study_id}/factors")))

    def analyses(self, study_id: str) -> Results[AnalysisRecord]:
        """Fetch a study's analysis records."""
        rows = normalize_rows(self._get(f"study/study_id/{study_id}/analysis"), "study_id")
        return Results(AnalysisRecord.model_validate(row) for row in rows)

    def metabolites(self, study_id: str) -> Results[MetaboliteRow]:
        """Fetch a study's identified metabolites.

        The endpoint returns an empty list for the largest studies, so an
        empty result means "not listed", not necessarily "none".
        """
        return Results(parse_metabolites(self._get(f"study/study_id/{study_id}/metabolites")))

    def inspect(
        self,
        study_ids: Sequence[str | StudySummary],
        *,
        workers: int = 4,
        include_summary: bool = True,
        include_samples: bool = True,
        include_analyses: bool = True,
        include_metabolites: bool = True,
        on_error: Literal["collect", "raise"] = "collect",
    ) -> Results[StudyDetail]:
        """Fetch deep per-study data, selecting which parts to include.

        Best-effort by default: a study that fails after retries is returned as
        a :class:`StudyDetail` with an ``error`` message and empty data, so a
        long batch always yields one result per input. Pass
        ``on_error="raise"`` to abort on the first failure instead. A batch
        the server throttled (or that lost studies) emits a ``UserWarning``;
        :attr:`stats` has the counters.

        A passed :class:`StudySummary` is reused as the detail's summary and is
        never refetched; a plain accession is fetched only when
        ``include_summary`` is set. Narrowing the ``include_*`` flags skips the
        endpoints you do not need — e.g. samples-only inspection makes one
        request per study instead of four.

        Args:
            study_ids: Accessions, or summaries to reuse.
            workers: Parallel study fetches. The default (4) is the measured
                sweet spot for corpus-scale runs; higher values trip server
                throttling and are counterproductive. Raise it only for small
                batches.
            include_summary: Include each study's summary. For a passed
                summary this only controls whether it is kept, not whether it
                is fetched.
            include_samples: Fetch the ``factors`` endpoint.
            include_analyses: Fetch the ``analysis`` endpoint.
            include_metabolites: Fetch the ``metabolites`` endpoint.
            on_error: ``"collect"`` (default) records per-study errors;
                ``"raise"`` propagates the first failure.

        Returns:
            One :class:`StudyDetail` per input, in input order.
        """
        targets: list[tuple[str, StudySummary | None]] = [
            (item.study_id, item) if isinstance(item, StudySummary) else (item, None)
            for item in study_ids
        ]
        if not targets:
            return Results()

        def _run(target: tuple[str, StudySummary | None]) -> StudyDetail:
            study_id, passed = target
            try:
                summary = None
                if include_summary:
                    summary = passed if passed is not None else self.study(study_id)
                return StudyDetail(
                    study_id=study_id,
                    summary=summary,
                    samples=list(self.samples(study_id)) if include_samples else [],
                    analyses=list(self.analyses(study_id)) if include_analyses else [],
                    metabolites=(
                        list(self.metabolites(study_id)) if include_metabolites else []
                    ),
                )
            except MetaboSearchError as exc:
                if on_error == "raise":
                    raise
                return StudyDetail(study_id=study_id, error=str(exc))

        before = self._http.stats
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            details = list(pool.map(_run, targets))
        warn_if_throttled(
            self._http.stats - before,
            errors=sum(1 for detail in details if detail.error),
            workers=workers,
            action="Workbench.inspect",
        )
        return Results(details)

    def ingest(self, obj: object, ws: Workspace) -> IngestReport:
        """Write study summaries or inspected details into a workspace.

        Args:
            obj: Models or a ``Results`` sequence accepted by
                :func:`metabo_search.workbench.ingest.batch` —
                :class:`StudySummary` or :class:`StudyDetail`.
            ws: The workspace to write to.

        Returns:
            Per-table row counts, plus the ids of any errored ``StudyDetail``
            items that were skipped.
        """
        import metabo_search.workbench.ingest as _ingest

        return _ingest.write(ws, obj)

    @property
    def stats(self) -> HttpStats:
        """Cumulative HTTP attempt statistics: attempts, throttling, backoff."""
        return self._http.stats

    def reset_stats(self) -> None:
        """Reset :attr:`stats` to zero."""
        self._http.reset_stats()

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._http.close()

    def __enter__(self) -> Workbench:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- internals -------------------------------------------------------

    def _get(self, path: str) -> object:
        response = self._http.request("GET", path)
        try:
            return response.json()
        except ValueError as exc:
            raise ResponseValidationError(
                f"{response.request.url} returned non-JSON"
            ) from exc


def _parse_corpus(payload: object) -> dict[str, StudySummary]:
    """Parse a raw ``/ST/summary`` payload into ``{study_id: StudySummary}``."""
    rows = normalize_rows(payload, "study_id")
    return {
        summary.study_id: summary
        for summary in (StudySummary.model_validate(row) for row in rows)
    }


def _tokens(text: str) -> set[str]:
    """Lowercased word tokens (keeps numbers and internal apostrophes)."""
    return set(_TOKEN.findall(text.lower()))


def _overlap(query: set[str], title: set[str]) -> float:
    """Fraction of query tokens present in the title (0 when no query)."""
    if not query:
        return 0.0
    return len(query & title) / len(query)
