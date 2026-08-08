"""High-level orchestration: deterministic discovery with minimal AI.

Everything here is deterministic and needs no LLM once a
``RequirementProfile`` exists:

    profile.hard ──► search API args (server-side filter)
                  ──► shallow screen (drop hard fails on search-index data)
                  ──► deep-inspect ONLY survivors
                  ──► full deterministic scoring → comparison report
"""

from __future__ import annotations

from mtbls_agent.inspector import inspect_studies
from mtbls_agent.models import (
    ComparisonReport,
    FitnessScore,
    RequirementProfile,
    ScoredCandidate,
)
from mtbls_agent.scorer import score_studies, screen_candidates
from mtbls_agent.searcher import profile_to_search_args, search_studies
from mtbls_agent.summarizer import build_comparison_table


def find_datasets(
    query: str = "",
    *,
    profile: RequirementProfile | None = None,
    max_candidates: int = 100,
    deep_inspect_top: int = 10,
    max_workers: int = 10,
    min_survivors: int | None = None,
    organism: str | list[str] | None = None,
    technique: str | list[str] | None = None,
    sample_type: str | list[str] | None = None,
    min_samples: int | None = None,
) -> ComparisonReport:
    """End-to-end discovery, deterministic when ``profile`` is structured.

    Pipeline (no LLM involved):
    1. Push hard requirements into the search API (server-side filter).
    2. Screen the shallow results: drop hard-fails on search-index data,
       rank survivors by a deterministic soft score.
    3. Deep-inspect only the top ``deep_inspect_top`` survivors — the slow
       network step runs on a far smaller, already-qualified set.
    4. Full deterministic scoring + comparison table.

    Parameters
    ----------
    query : str
        Free-text relevance hint (optional if ``profile`` is complete).
    profile : RequirementProfile | None
        Hard + nice-to-have requirements.  If None, only search + inspect.
    max_candidates : int
        Max shallow candidates to fetch from the API.
    deep_inspect_top : int
        How many screened survivors to deep-inspect (default 10).
    max_workers : int
        Parallel deep-inspection workers.
    min_survivors : int | None
        Minimum screened survivors to aim for (defaults to deep_inspect_top).
    organism, technique, sample_type, min_samples :
        Manual shortcuts; override/merge with profile-derived filters.

    Returns
    -------
    ComparisonReport
        Ranked candidates + comparison table.  ``report.screening`` holds the
        deterministic pre-screen result (survivors, dropped-with-reasons).
    """
    # 1) Server-side filter from hard requirements (+ manual overrides)
    args = profile_to_search_args(profile) if profile is not None else {}
    if organism is not None:
        args["organism"] = organism
    if technique is not None:
        args["technique"] = technique
    if sample_type is not None:
        args["sample_type"] = sample_type
    if min_samples is not None:
        args["min_samples"] = min_samples

    candidates = search_studies(
        query=query,
        page_size=min(max_candidates, 100),
        max_results=max_candidates,
        **args,
    )

    if not candidates:
        return ComparisonReport(candidates=[])

    # 2) Deterministic shallow pre-screen
    screening = None
    if profile is not None:
        target = min_survivors if min_survivors is not None else deep_inspect_top
        screening = screen_candidates(candidates, profile, min_survivors=target)
        to_inspect = screening.survivors
    else:
        to_inspect = candidates

    if not to_inspect:
        # Nothing survives the hard constraints — report empty but carry the
        # screening reasons so the agent can tell the user *why*.
        report = build_comparison_table([], profile=profile)
        report.screening = screening if screening is not None else None
        return report

    # 3) Deep-inspect only the survivors (the slow step, on a small set)
    deep_candidates = inspect_studies(to_inspect[:deep_inspect_top],
                                      max_workers=max_workers)

    # 4) Full deterministic scoring
    scored = (
        score_studies(deep_candidates, profile)
        if profile is not None
        else [ScoredCandidate(candidate=c, score=FitnessScore())
              for c in deep_candidates]
    )

    report = build_comparison_table(scored, profile=profile)
    report.screening = screening if screening is not None else None
    return report