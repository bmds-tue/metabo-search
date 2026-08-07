"""High-level orchestration: search → inspect → score → summarize."""

from __future__ import annotations

from mtbls_agent.inspector import inspect_studies
from mtbls_agent.models import (
    ComparisonReport,
    RequirementProfile,
    ScoredCandidate,
    StudyCandidate,
)
from mtbls_agent.scorer import score_studies
from mtbls_agent.searcher import search_studies
from mtbls_agent.summarizer import build_comparison_table


def find_datasets(
    query: str = "",
    *,
    profile: RequirementProfile | None = None,
    max_candidates: int = 100,
    deep_inspect_top: int = 10,
    max_workers: int = 10,
    organism: str | list[str] | None = None,
    technique: str | list[str] | None = None,
    sample_type: str | list[str] | None = None,
    min_samples: int | None = None,
) -> ComparisonReport:
    """End-to-end: search MetaboLights, deep-inspect top candidates, score, compare.

    This is the main entry point for the AI agent.  It wraps the full
    pipeline in a single call for simple use cases.

    Parameters
    ----------
    query : str
        Free-text search query.
    profile : RequirementProfile | None
        Structured requirements (hard + nice-to-have).  If None, only
        search + inspect is performed (no scoring).
    max_candidates : int
        Maximum Phase-1 candidates to fetch.
    deep_inspect_top : int
        Number of promising candidates to deep-inspect (Phase 2).
    max_workers : int
        Parallel workers for deep inspection.
    organism, technique, sample_type, min_samples :
        Shortcut filters applied during Phase 1 search.

    Returns
    -------
    ComparisonReport
        Scored and ranked candidates with a comparison table.
    """
    # Phase 1: broad search
    candidates = search_studies(
        query=query,
        page_size=min(max_candidates, 100),
        max_results=max_candidates,
        organism=organism,
        technique=technique,
        sample_type=sample_type,
        min_samples=min_samples,
    )

    if not candidates:
        return ComparisonReport(candidates=[])

    # Phase 2: deep inspect top candidates
    deep_candidates = candidates[:deep_inspect_top]
    if deep_candidates:
        deep_candidates = inspect_studies(deep_candidates, max_workers=max_workers)

    # Score
    if profile:
        scored = score_studies(deep_candidates, profile)
    else:
        scored = [ScoredCandidate(candidate=c, score=__import__('mtbls_agent.models', fromlist=['FitnessScore']).FitnessScore()) for c in deep_candidates]

    # Build report
    return build_comparison_table(scored, profile=profile)