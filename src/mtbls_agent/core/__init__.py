"""core — typed pipeline over the mtbls_agent function library.

Design: docs/design-pipeline.md.  Public surface:

    from mtbls_agent import (pipeline, search, filter, screen, maf, custom,
        inspect, score, describe, download, export, register_predicate,
        quick_probe, quick_discovery, full_report, harvest, ...)
"""

from mtbls_agent.core.results import (
    SearchResult, FilterResult, InspectResult, ScoreResult,
    DescribeResult, DownloadResult, ExportResult,
)
from mtbls_agent.core.steps import (
    CacheOpts, PrintOpts, Pipeline, PipelineResult,
    Screen, Maf, Custom,
    pipeline, search, filter, screen, maf, custom,
    inspect, score, describe, download, export,
    register_predicate, registered_predicates,
)
from mtbls_agent.core.recipes import (
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