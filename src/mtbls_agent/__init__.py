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
from mtbls_agent.inspector import inspect_studies, load_study_from_isa
from mtbls_agent.scorer import score_studies, screen_candidates
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

__all__ = [
    # Functions (the first move)
    "search_studies",
    "inspect_studies",
    "load_study_from_isa",
    "score_studies",
    "screen_candidates",
    "profile_to_search_args",
    "build_comparison_table",
    "find_datasets",
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
]
