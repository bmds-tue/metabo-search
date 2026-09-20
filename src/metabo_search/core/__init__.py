"""core — typed pipeline over the metabo_search function library.

Design: docs/design-pipeline.md.  Public surface:

    from metabo_search import (pipeline, search, filter, screen, maf, custom,
        inspect, score, describe, download, export, register_predicate,
        quick_probe, quick_discovery, full_report, harvest, ...)
"""

from metabo_search.core.results import (
    SearchResult, FilterResult, InspectResult, ScoreResult,
    DescribeResult, DownloadResult, ExportResult,
)
from metabo_search.core.steps import (
    CacheOpts, PrintOpts, Pipeline, PipelineResult,
    Screen, Maf, Custom,
    pipeline, search, filter, screen, maf, custom,
    inspect, score, describe, download, export,
    register_predicate, registered_predicates,
)
from metabo_search.core.recipes import (
    quick_probe, quick_discovery, full_report, harvest,
)

__all__ = [
    "SearchResult", "FilterResult", "InspectResult", "ScoreResult",
    "DescribeResult", "DownloadResult", "ExportResult",
    "CacheOpts", "PrintOpts", "Pipeline", "PipelineResult",
    "Screen", "Maf", "Custom",
    "pipeline", "search", "filter", "screen", "maf", "custom",
    "inspect", "score", "describe", "download", "export",
    "register_predicate", "registered_predicates",
    "quick_probe", "quick_discovery", "full_report", "harvest",
]