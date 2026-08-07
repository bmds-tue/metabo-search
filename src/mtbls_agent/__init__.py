"""mtbls-agent: AI-native search for MetaboLights datasets.

Usage (by AI agent or in scripts):

    from mtbls_agent import (
        search_studies,           # Phase 1: broad search
        inspect_studies,          # Phase 2: parallel deep dive
        score_studies,            # Hard filters + soft scoring
        build_comparison_table,   # Structured comparison report
        find_datasets,            # End-to-end pipeline
        RequirementProfile,
        StudyRequirements,
    )
"""

from mtbls_agent.models import (
    AssayInfo,
    ComparisonReport,
    DataFileInfo,
    FitnessScore,
    OntologyTerm,
    ProtocolInfo,
    PublicationInfo,
    RequirementProfile,
    ScoredCandidate,
    StudyCandidate,
    StudyRequirements,
)
from mtbls_agent.searcher import search_studies
from mtbls_agent.inspector import inspect_studies
from mtbls_agent.scorer import score_studies
from mtbls_agent.summarizer import build_comparison_table
from mtbls_agent.workflow import find_datasets
from mtbls_agent.downloader import (
    list_data_files,
    download_data_files,
    start_download,
    DownloadConfig,
    DataFileRef,
    DownloadResult,
    DownloadTask,
)
from mtbls_agent.manifest import SampleManifest
from mtbls_agent.sample_summarizer import (
    SampleSentence,
    build_sample_sentences,
)

__all__ = [
    "RequirementProfile",
    "StudyRequirements",
    "StudyCandidate",
    "AssayInfo",
    "DataFileInfo",
    "OntologyTerm",
    "PublicationInfo",
    "ProtocolInfo",
    "FitnessScore",
    "ScoredCandidate",
    "ComparisonReport",
    "search_studies",
    "inspect_studies",
    "score_studies",
    "build_comparison_table",
    "find_datasets",
    "SampleSentence",
    "build_sample_sentences",
    "list_data_files",
    "download_data_files",
    "DownloadConfig",
    "DataFileRef",
    "DownloadResult",
    "start_download",
    "DownloadTask",
    "SampleManifest",
]