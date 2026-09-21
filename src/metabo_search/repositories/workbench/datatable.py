"""datatable → MAF-shape normalization (identified-metabolite tables).

v1 keeps this thin: the deep pass records ``metabolite_count`` from the
``/metabolites`` endpoint; full ``datatable`` parsing (fullest-column
counting, named/identified/mz annotation levels, per-analysis fetching)
lands with the MAF adapter milestone so scoring semantics match the MAF
path exactly.
"""

from __future__ import annotations

from metabo_search.models import StudyCandidate


def datatable_rows(candidate: StudyCandidate) -> list[dict]:
    """Rows of the per-study identified-metabolite table, or [].

    Not fetched by default in v1 — returns [] until the datatable fetch +
    MAF adapter milestone lands (see docs/implementation-checklist.md).
    """
    return []
