"""find_datasets — end-to-end discovery.

Now a thin shim over the typed pipeline (docs/design-pipeline.md): same
signature and behavior, but the orchestration lives in core.  New code should
use ``quick_discovery`` / ``full_report`` / ``pipeline(...)`` instead.
"""

from __future__ import annotations

from metabo_search.core.steps import (
    filter, inspect, pipeline, score, screen, search,
)
from metabo_search.models import ComparisonReport
from metabo_search.scorer import ScreeningResult


def find_datasets(
    query: str = "",
    *,
    profile=None,
    databases=None,
    max_candidates: int = 100,
    deep_inspect_top: int = 10,
    max_workers: int = 10,
    min_survivors: int | None = None,
    organism=None,
    technique=None,
    sample_type=None,
    min_samples: int | None = None,
) -> ComparisonReport:
    """End-to-end discovery, deterministic when ``profile`` is structured.

    Pipeline (no LLM involved):
    1. Push hard requirements into the search API (server-side filter).
    2. Screen the shallow results: drop hard-fails, rank survivors.
    3. Deep-inspect only the top survivors (bounded by the screen cap).
    4. Full deterministic scoring + comparison table.

    The legacy entry point — equivalent to ``quick_discovery(...).run()``.
    """
    manual = {
        "organism": organism, "technique": technique,
        "sample_type": sample_type, "min_samples": min_samples,
    }
    manual = {k: v for k, v in manual.items() if v is not None}

    target = min_survivors if min_survivors is not None else deep_inspect_top
    if profile is not None:
        steps = [filter(screen(profile=profile, min_survivors=target))]
    elif target:
        steps = [filter(screen(min_survivors=target))]
    else:
        steps = []
    steps += [inspect(workers=max_workers), score(profile=profile)]

    p = pipeline(
        search(query, profile=profile, databases=databases,
               page_size=min(max_candidates, 100),
               max_results=max_candidates, **manual),
        *steps,
    )
    r = p.run()

    report = r["score"].table or ComparisonReport(candidates=[])
    filt = r.get("filter")
    report.screening = (
        ScreeningResult(survivors=filt.survivors, dropped=filt.dropped,
                        shallow_scores=filt.order)
        if filt is not None else None
    )
    return report