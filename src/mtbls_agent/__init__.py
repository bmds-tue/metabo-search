"""mtbls-agent: AI-native search + enrichment for MetaboLights datasets.

One import surface.  The main jobs:

    from mtbls_agent import (
        find_datasets,              # search + inspect + score + compare
        prepare_samples,            # per-sample sentences: get ONE prompt
        load_samples,               #   ... load cached, or
        submit_samples,             #   ... give back LLM text -> sentences, cached
        list_data_files,            # Download:
        download_data_files,        #   selective download
        SampleManifest,             # Export: flat CSV + traceability
        RequirementProfile, StudyRequirements,
        DownloadConfig,
        StudyCandidate,
    )
"""

from mtbls_agent.searcher import search_studies, profile_to_search_args
from mtbls_agent.inspector import inspect_studies, load_study_from_isa, download_maf_files
from mtbls_agent.scorer import score_studies, screen_candidates, filter_by_maf
from mtbls_agent.maf import analyze_maf_files, render_maf_summary, MafAnalysis
from mtbls_agent.summarizer import build_comparison_table
from mtbls_agent.workflow import find_datasets

from mtbls_agent.sample_gen import (
    prepare_samples,
    load_samples,
    submit_samples,
    revise_samples,
    SampleTask,
    collect_sample_contexts,
    build_study_profile_prompt,
    parse_study_profile,
    apply_recipe,
    SampleSentencesStore,
)

from mtbls_agent.downloader import (
    list_data_files,
    download_data_files,
    start_download,
    format_summary,
    DownloadConfig,
)
from mtbls_agent.manifest import SampleManifest

from mtbls_agent.models import (
    RequirementProfile,
    StudyRequirements,
    StudyCandidate,
)

# ── core: typed pipeline (docs/design-pipeline.md) ──
from mtbls_agent.core import (  # noqa: E402  (isort: keep above __all__)
    Pipeline, PipelineResult, CacheOpts, PrintOpts,
    Screen, Maf, Custom,
    SearchResult, FilterResult, InspectResult, ScoreResult,
    DescribeResult, DownloadResult, ExportResult,
    pipeline, search, filter, screen, maf, custom,
    inspect, score, describe, download, export,
    register_predicate, registered_predicates,
    quick_probe, quick_discovery, full_report, harvest,
)

__all__ = [
    # Functions (the first move)
    "search_studies",
    "inspect_studies",
    "load_study_from_isa",
    "score_studies",
    "screen_candidates",
    "filter_by_maf",
    "profile_to_search_args",
    "build_comparison_table",
    "find_datasets",
    "download_maf_files",
    "analyze_maf_files",
    "render_maf_summary",
    "MafAnalysis",
    # Per-sample sentences (one-round-trip)
    "prepare_samples",
    "load_samples",
    "submit_samples",
    "revise_samples",
    "SampleTask",
    "SampleSentencesStore",
    "collect_sample_contexts",
    "build_study_profile_prompt",
    "parse_study_profile",
    "apply_recipe",
    # Download + export
    "list_data_files",
    "download_data_files",
    "start_download",
    "format_summary",
    "DownloadConfig",
    "SampleManifest",
    # You construct these
    "RequirementProfile",
    "StudyRequirements",
    "StudyCandidate",
    # core: typed pipeline
    "Pipeline", "PipelineResult", "CacheOpts", "PrintOpts",
    "Screen", "Maf", "Custom",
    "SearchResult", "FilterResult", "InspectResult", "ScoreResult",
    "DescribeResult", "DownloadResult", "ExportResult",
    "pipeline", "search", "filter", "screen", "maf", "custom",
    "inspect", "score", "describe", "download", "export",
    "register_predicate", "registered_predicates",
    "quick_probe", "quick_discovery", "full_report", "harvest",
]
