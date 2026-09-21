"""The repository seam — a small per-data-source interface.

Every repository (MetaboLights, Metabolomics Workbench, …) implements the
same operations against its backend and emits the shared ``StudyCandidate``
model, tagged with ``candidate.repository == repo.name``.  The pipeline
dispatches on that tag; repository-specific code never leaks upward.

Contracts
---------
- ``search`` returns *shallow* candidates (search-index / corpus level):
  identity, title, organisms, organism_parts, sample_count, design
  descriptors.  It must fill enough fields for deterministic screening.
- ``deep_metadata`` takes shallow candidates of *this* repository only and
  returns deep versions (assays, sample_metadata, metabolite_count, deep
  flags).  Implementors may fetch in parallel; the pipeline bounds workers.
- Any operation the repository cannot provide raises ``NotImplementedError``
  with a clear message so the pipeline fails loudly, never silently.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:  # pragma: no cover — typing only
    from metabo_search.models import RequirementProfile, StudyCandidate

# Canonical repository names.  ``databases=`` uses these; unknown names are a
# ValueError at pipeline validation time.
REPOSITORY_METABOLIGHTS = "metabolights"
REPOSITORY_WORKBENCH = "metabolomics_workbench"

DEFAULT_DATABASES = (REPOSITORY_METABOLIGHTS, REPOSITORY_WORKBENCH)
"""The ``databases`` default — both repositories, searched in this order."""


class StudyRepository(ABC):
    """One data source behind the shared pipeline.

    Subclasses: implement ``name`` and at least ``search``.  The others
    raise ``NotImplementedError`` until provided.
    """

    name: ClassVar[str] = ""

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        profile: RequirementProfile | None = None,
        page_size: int = 100,
        max_results: int = 200,
        filters: list[dict[str, Any]] | None = None,
        sort_field: str | None = None,
        sort_direction: str = "desc",
        ms_filters: dict[str, Any] | None = None,
        organism: str | list[str] | None = None,
        technique: str | list[str] | None = None,
        sample_type: str | list[str] | None = None,
        min_samples: int | None = None,
        min_raw_files: int | None = None,
        cache_root: str | None = None,
    ) -> tuple[list[StudyCandidate], dict] | list[StudyCandidate]:
        """Broad search → shallow candidates.

        Parameters mirror ``metabo_search.search_studies`` so callers can
        treat every repository the same.  ``cache_root`` (when given) is the
        pipeline cache root: heavyweight corpora may cache under it
        (e.g. a whole-index summary).  Must return candidates tagged with
        ``repository=self.name``.

        Returns
        -------
        ``(candidates, meta)`` — ``meta`` is a plain dict the search step
        merges into ``SearchResult.args_used`` (ambiguity notices, slot
        decisions, counts).  A bare ``list`` is tolerated (adds empty meta).
        """

    def deep_metadata(
        self,
        candidates: list[StudyCandidate],
        *,
        workers: int = 10,
        tmp_dir: str | None = None,
        parse_workers: int | None = None,
        cache_root: str | None = None,
    ) -> list[StudyCandidate]:
        """Deep-inspect candidates of this repository (parallel)."""
        raise NotImplementedError(
            f"{self.name} does not implement deep inspection yet")

    def sample_rows(self, candidate: StudyCandidate) -> list[dict[str, str]]:
        """Per-sample rows (factors / ISA sample sheet) for describe/manifest."""
        raise NotImplementedError(
            f"{self.name} does not expose sample rows yet")

    def metabolite_table(
        self, candidate: StudyCandidate
    ) -> Any:
        """Named-metabolite table (MAF / datatable) for scoring, or None."""
        raise NotImplementedError(
            f"{self.name} does not expose a metabolite table yet")

    def download(
        self,
        candidates: list[StudyCandidate],
        dest_dir: str,
        *,
        files_cache_dir: str | None = None,
    ) -> None:
        raise NotImplementedError(
            f"{self.name} does not support downloads yet")


# ──────────────────────────────────────────────────────────────
# Registry + dispatch helpers
# ──────────────────────────────────────────────────────────────

DISPATCH: dict[str, StudyRepository] = {}
"""Registered repositories by name — populated at import of each subpackage."""


def validate_databases(databases) -> tuple[str, ...]:
    """Normalize + validate a ``databases`` value.

    Returns the names as an ordered tuple.  Raises
    ``ValueError`` for unknown names or an empty selection.

    Parameters
    ----------
    databases : str | iterable[str] | None
        ``None`` → ``DEFAULT_DATABASES``; a single name (str) or an
        iterable of names.
    """
    if databases is None:
        names: tuple[str, ...] = DEFAULT_DATABASES
    elif isinstance(databases, str):
        names = (databases,)
    else:
        names = tuple(databases)
    if not names:
        raise ValueError("databases must name at least one repository")
    known = set(DISPATCH)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(
            f"unknown database(s) {unknown!r} — known: {sorted(known)}")
    return names


def database_from_id(study_id: str) -> str:
    """Which repository owns a candidate id.

    Rules: MetaboLights ids start with ``"MTBLS"`` (case-insensitive);
    everything else is assumed Metabolomics Workbench (ST/AN ids).  Not
    used for correctness (ids are disjoint); primarily for messaging.
    """
    if study_id.upper().startswith("MTBLS"):
        return REPOSITORY_METABOLIGHTS
    return REPOSITORY_WORKBENCH