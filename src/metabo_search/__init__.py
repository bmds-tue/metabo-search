"""metabo-search: AI-native search + enrichment for MetaboLights datasets.

One import surface.  The main jobs:

    from metabo_search import (
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

from metabo_search.searcher import search_studies, profile_to_search_args
from metabo_search.inspector import inspect_studies, load_study_from_isa, download_maf_files
from metabo_search.scorer import score_studies, screen_candidates, filter_by_maf
from metabo_search.maf import analyze_maf_files, render_maf_summary, MafAnalysis
from metabo_search.summarizer import build_comparison_table
from metabo_search.workflow import find_datasets

from metabo_search.sample_gen import (
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

from metabo_search.downloader import (
    list_data_files,
    download_data_files,
    start_download,
    format_summary,
    DownloadConfig,
)
from metabo_search.manifest import SampleManifest

from metabo_search.models import (
    RequirementProfile,
    StudyRequirements,
    StudyCandidate,
    OntologyTerm,
)

# ── repositories: register available data sources (populates DISPATCH) ──
from metabo_search import repositories  # noqa: E402
from metabo_search.repositories import metabolights  # noqa: E402
from metabo_search.repositories import workbench  # noqa: E402
from metabo_search.repositories.base import (  # noqa: E402
    DEFAULT_DATABASES,
)

# ── core: typed pipeline (docs/design-pipeline.md) ──
from metabo_search.core import (  # noqa: E402  (isort: keep above __all__)
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
    "OntologyTerm",  # nested in candidates (organism/organism_parts)
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
