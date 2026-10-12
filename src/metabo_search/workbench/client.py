"""Client for the Metabolomics Workbench REST API.

The Workbench has no server-side free-text search: :meth:`Workbench.search`
fetches the whole-index summary corpus (slow, ~90 s) once per client instance
and filters it locally. Per-study methods fetch the ``factors``, ``analysis``,
and ``metabolites`` endpoints.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from types import TracebackType
from typing import Literal

import httpx

from metabo_search.errors import MetaboSearchError, NotFound, ResponseValidationError
from metabo_search.results import Results
from metabo_search.transport import HttpClient, HttpRecord, RateLimit, RetryPolicy
from metabo_search.workbench.models import (
    AnalysisRecord,
    MetaboliteRow,
    SampleRow,
    StudyDetail,
    StudySummary,
)
from metabo_search.workbench.parse import normalize_rows, parse_metabolites, parse_samples

BASE_URL = "https://www.metabolomicsworkbench.org/rest"
CORPUS_TIMEOUT = 180.0

_TOKEN = re.compile(r"[a-z0-9']+")


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
        self._summaries: dict[str, StudySummary] | None = None

    def all_studies(self) -> dict[str, StudySummary]:
        """Return the whole-index study summaries, fetched once per client.

        Returns:
            Study id to summary. Cached on the instance after the first call.
        """
        if self._summaries is None:
            rows = normalize_rows(self._get("study/study_id/ST/summary"), "study_id")
            self._summaries = {
                summary.study_id: summary
                for summary in (StudySummary.model_validate(row) for row in rows)
            }
        return self._summaries

    def search(
        self, query: str = "", *, min_samples: int | None = None, limit: int = 25
    ) -> Results[StudySummary]:
        """Search the whole-index summaries by title term overlap.

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

        Args:
            study_id: Workbench accession, e.g. ``"ST000001"``.

        Returns:
            The study summary.

        Raises:
            NotFound: No study with that accession exists.
        """
        assert study_id, "study_id must be non-empty"
        rows = normalize_rows(self._get(f"study/study_id/{study_id}/summary"), "study_id")
        if not rows:
            raise NotFound(f"study {study_id!r} not found")
        return StudySummary.model_validate(rows[0])

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
        workers: int = 8,
        on_error: Literal["collect", "raise"] = "collect",
    ) -> Results[StudyDetail]:
        """Fetch deep per-study data (factors, analyses, metabolites).

        Best-effort by default: a study that fails after retries is returned as
        a :class:`StudyDetail` with an ``error`` message and empty data, so a
        long batch always yields one result per input. Pass
        ``on_error="raise"`` to abort on the first failure instead.

        Args:
            study_ids: Accessions, or summaries to read accessions from.
            workers: Parallel study fetches.
            on_error: ``"collect"`` (default) records per-study errors;
                ``"raise"`` propagates the first failure.

        Returns:
            One :class:`StudyDetail` per input, in input order.
        """
        ids = [item if isinstance(item, str) else item.study_id for item in study_ids]
        if not ids:
            return Results()

        def _run(study_id: str) -> StudyDetail:
            try:
                return StudyDetail(
                    study_id=study_id,
                    summary=self.study(study_id),
                    samples=list(self.samples(study_id)),
                    analyses=list(self.analyses(study_id)),
                    metabolites=list(self.metabolites(study_id)),
                )
            except MetaboSearchError as exc:
                if on_error == "raise":
                    raise
                return StudyDetail(study_id=study_id, error=str(exc))

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            return Results(pool.map(_run, ids))

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


def _tokens(text: str) -> set[str]:
    """Lowercased word tokens (keeps numbers and internal apostrophes)."""
    return set(_TOKEN.findall(text.lower()))


def _overlap(query: set[str], title: set[str]) -> float:
    """Fraction of query tokens present in the title (0 when no query)."""
    if not query:
        return 0.0
    return len(query & title) / len(query)
