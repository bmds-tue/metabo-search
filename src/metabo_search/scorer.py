"""Score studies against a RequirementProfile.

Hard requirements → pass/fail filter.
Nice-to-haves → scored 0-1 per criterion → combined overall score (0-1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from metabo_search.models import (
    FitnessScore,
    RequirementProfile,
    ScoredCandidate,
    StudyCandidate,
)


# Criteria checkable on shallow (search-index) data only.  Ionization and
# data formats need deep inspection, so the screen stage never hard-fails on
# them — they are enforced after inspection by _score_one.
SHALLOW_HARD_CRITERIA = (
    "organisms", "sample_types", "techniques",
    "min_samples", "has_raw_data", "analysis_types",
)


@dataclass
class ScreeningResult:
    """Outcome of the deterministic shallow pre-screen (no network)."""

    survivors: list[StudyCandidate]
    """Candidates that pass all shallow-checkable hard constraints, sorted by
    shallow soft score (desc)."""
    dropped: list[tuple[StudyCandidate, str]]
    """(candidate, reason) for candidates that failed a hard constraint."""
    shallow_scores: dict[str, float]
    """study_id -> deterministic shallow relevance score (0-1)."""


def screen_candidates(
    candidates: list[StudyCandidate],
    profile: RequirementProfile,
    min_survivors: int = 10,
) -> ScreeningResult:
    """Deterministic pre-screen on search-index data (no LLM, no download).

    Drops candidates that fail shallow-checkable hard constraints, ranks the
    rest by a shallow soft score, and keeps at least ``min_survivors`` (or the
    total that pass) for deep inspection.

    Deterministic and cheap: only fields already present in the search hit.
    """
    hard = profile.hard
    survivors: list[StudyCandidate] = []
    dropped: list[tuple[StudyCandidate, str]] = []
    scores: dict[str, float] = {}

    for cand in candidates:
        reason = _shallow_hard_fail(cand, hard)
        if reason:
            dropped.append((cand, reason))
            continue
        s = _shallow_score(cand, profile)
        scores[cand.study_id] = s
        survivors.append(cand)

    survivors.sort(key=lambda c: scores.get(c.study_id, 0.0), reverse=True)
    if len(survivors) > min_survivors:
        survivors = survivors[:min_survivors]
    return ScreeningResult(survivors=survivors, dropped=dropped,
                           shallow_scores=scores)


def filter_by_maf(
    candidates: list[StudyCandidate],
    *,
    require_maf: bool = True,
    min_metabolites: int | None = None,
) -> list[StudyCandidate]:
    """Post-inspection filter on MAF (metabolite assignment file) presence.

    Runs on DEEP-inspected candidates (the search index does not expose MAF
    files — they are only known after :func:`inspect_studies`).  Keeps
    candidates in input order.

    Parameters
    ----------
    candidates : list[StudyCandidate]
        Deep-inspected candidates (``maf_files_parsed`` populated).
    require_maf : bool
        True: keep only studies that ship ≥1 MAF.  False: keep only studies
        with NO MAF.
    min_metabolites : int | None
        If set, also require ``metabolite_count >= min_metabolites``.

    Returns
    -------
    list[StudyCandidate]
        Filtered subset, input order preserved.
    """
    kept: list[StudyCandidate] = []
    for c in candidates:
        has_maf = c.maf_files_parsed and (c.metabolite_count or 0) > 0
        if has_maf != require_maf:
            continue
        if min_metabolites is not None and (c.metabolite_count or 0) < min_metabolites:
            continue
        kept.append(c)
    return kept


def _shallow_hard_fail(cand: StudyCandidate, hard) -> str | None:
    """Return a failure reason string if a shallow-checkable hard constraint
    fails, else None.  Ionization + data formats are deliberately skipped
    (need deep data)."""
    if hard.organisms:
        ok, _ = _check_terms(cand.organisms, hard.organisms, mode="any")
        if not ok:
            return f"organism (need {hard.organisms})"
    if hard.sample_types:
        ok, _ = _check_terms(cand.organism_parts, hard.sample_types, mode="any")
        if not ok:
            return f"sample_type (need {hard.sample_types})"
    if hard.techniques:
        ok, _ = _check_techniques(cand, hard.techniques)
        if not ok:
            return f"technique (need {hard.techniques})"
    if hard.min_samples is not None and (cand.sample_count or 0) < hard.min_samples:
        return f"min_samples (got {cand.sample_count})"
    if hard.has_raw_data is True and not (cand.raw_file_count or 0):
        return "raw_data (none)"
    if hard.analysis_types:
        ok, _ = _check_design_descriptors(cand, hard.analysis_types)
        if not ok:
            return f"analysis_type (need {hard.analysis_types})"
    return None


def _shallow_score(cand: StudyCandidate, profile: RequirementProfile) -> float:
    """Deterministic shallow relevance: nice-to-have terms + free-text overlap.

    Uses only search-index fields so it costs nothing and is reproducible."""
    nice = profile.nice_to_have
    parts: list[float] = []
    if nice.organisms:
        s, _ = _score_terms(cand.organisms, nice.organisms)
        parts.append(s)
    if nice.sample_types:
        s, _ = _score_terms(cand.organism_parts, nice.sample_types)
        parts.append(s)
    if nice.techniques:
        s, _ = _score_techniques(cand, nice.techniques)
        parts.append(s)
    if profile.free_text:
        s, _ = _score_text_relevance(cand, profile.free_text)
        parts.append(s)
    # min_samples soft coverage
    if nice.min_samples is not None:
        actual = cand.sample_count or 0
        parts.append(min(1.0, actual / max(1, nice.min_samples)))
    return sum(parts) / len(parts) if parts else 0.5


def score_studies(
    candidates: list[StudyCandidate],
    profile: RequirementProfile,
) -> list[ScoredCandidate]:
    """Score each candidate against the requirement profile.

    Returns scored candidates sorted descending by overall score.
    Candidates that fail a hard requirement are at the bottom (score=0).
    """
    scored: list[ScoredCandidate] = []

    for c in candidates:
        score = _score_one(c, profile)
        scored.append(ScoredCandidate(candidate=c, score=score))

    # Sort: hard-passed first (by overall score), then hard-failed (by score)
    scored.sort(key=lambda s: (s.score.hard_passed, s.score.overall), reverse=True)
    return scored


def _score_one(
    candidate: StudyCandidate, profile: RequirementProfile
) -> FitnessScore:
    """Compute fitness for a single candidate."""
    hard = profile.hard
    nice = profile.nice_to_have

    score = FitnessScore()
    hard_fails: list[str] = []
    per_criterion: dict[str, float] = {}
    explanations: dict[str, str] = {}

    # ── Hard requirements (pass/fail) ──
    if hard.organisms:
        passed, expl = _check_terms(candidate.organisms, hard.organisms, mode="any")
        if not passed:
            hard_fails.append(f"organism: needs {hard.organisms}, got {_term_names(candidate.organisms)}")
        per_criterion["organism (hard)"] = 1.0 if passed else 0.0
        explanations["organism (hard)"] = expl

    if hard.sample_types:
        passed, expl = _check_terms(
            candidate.organism_parts, hard.sample_types, mode="any"
        )
        if not passed:
            hard_fails.append(f"sample type: needs {hard.sample_types}")
        per_criterion["sample_type (hard)"] = 1.0 if passed else 0.0
        explanations["sample_type (hard)"] = expl

    if hard.techniques:
        passed, expl = _check_techniques(candidate, hard.techniques)
        if not passed:
            hard_fails.append(f"technique: needs {hard.techniques}")
        per_criterion["technique (hard)"] = 1.0 if passed else 0.0
        explanations["technique (hard)"] = expl

    if hard.min_samples is not None:
        actual = candidate.sample_count or 0
        if actual < hard.min_samples:
            hard_fails.append(
                f"min samples: needs ≥{hard.min_samples}, got {actual}"
            )
        per_criterion["min_samples (hard)"] = 1.0 if actual >= hard.min_samples else 0.0
        explanations["min_samples (hard)"] = f"{actual} samples (need ≥{hard.min_samples})"

    if hard.has_raw_data is True:
        raw = candidate.raw_file_count or 0
        if raw == 0:
            hard_fails.append("raw data: required but none found")
        per_criterion["raw_data (hard)"] = 1.0 if raw > 0 else 0.0
        explanations["raw_data (hard)"] = f"{raw} raw files"

    if hard.ionization_modes:
        passed, expl = _check_ionization(candidate, hard.ionization_modes)
        if not passed:
            hard_fails.append(f"ionization mode: needs {hard.ionization_modes}")
        per_criterion["ionization (hard)"] = 1.0 if passed else 0.0
        explanations["ionization (hard)"] = expl

    if hard.analysis_types:
        passed, expl = _check_design_descriptors(candidate, hard.analysis_types)
        if not passed:
            hard_fails.append(f"analysis type: needs {hard.analysis_types}")
        per_criterion["analysis_type (hard)"] = 1.0 if passed else 0.0
        explanations["analysis_type (hard)"] = expl

    if hard.data_formats:
        passed, expl = _check_data_formats(candidate, hard.data_formats)
        if not passed:
            hard_fails.append(f"data format: needs {hard.data_formats}")
        per_criterion["data_format (hard)"] = 1.0 if passed else 0.0
        explanations["data_format (hard)"] = expl

    if hard.has_maf is not None:
        has_maf = candidate.maf_files_parsed and (candidate.metabolite_count or 0) > 0
        if hard.has_maf is True and not has_maf:
            hard_fails.append("MAF: needs a metabolite assignment file")
        if hard.has_maf is False and has_maf:
            hard_fails.append("MAF: must NOT contain a metabolite assignment file")
        per_criterion["maf (hard)"] = 1.0 if has_maf == hard.has_maf else 0.0
        explanations["maf (hard)"] = (
            f"maf_files_parsed={candidate.maf_files_parsed}, "
            f"metabolite_count={candidate.metabolite_count}"
        )

    if hard.min_metabolites is not None:
        actual = candidate.metabolite_count or 0
        if candidate.metabolite_list_unavailable and \
                actual < hard.min_metabolites:
            # The count is UNKNOWN, not 0: the workbench /metabolites
            # endpoint OMITS the list for the largest studies (see
            # _metabolite_count).  Hard-failing those is wrong — surface the
            # caveat instead and let the agent decide.
            per_criterion["min_metabolites (hard)"] = 0.0
            explanations["min_metabolites (hard)"] = (
                f"metabolite list UNAVAILABLE (endpoint omitted it) — "
                f"count unknown, not 0; proxy numbers unavailable for "
                f"{candidate.study_id}")
        else:
            if actual < hard.min_metabolites:
                hard_fails.append(
                    f"min metabolites: needs ≥{hard.min_metabolites}, got {actual}"
                )
            per_criterion["min_metabolites (hard)"] = 1.0 if actual >= hard.min_metabolites else 0.0
            explanations["min_metabolites (hard)"] = f"{actual} metabolites (need ≥{hard.min_metabolites})"

    score.hard_fail_reasons = hard_fails
    score.hard_passed = len(hard_fails) == 0

    # ── Nice-to-have scoring (0-1) ──
    nice_scores: list[float] = []

    if nice.organisms:
        s, expl = _score_terms(candidate.organisms, nice.organisms)
        nice_scores.append(s)
        per_criterion["organism (nice)"] = s
        explanations["organism (nice)"] = expl

    if nice.sample_types:
        s, expl = _score_terms(candidate.organism_parts, nice.sample_types)
        nice_scores.append(s)
        per_criterion["sample_type (nice)"] = s
        explanations["sample_type (nice)"] = expl

    if nice.techniques:
        s, expl = _score_techniques(candidate, nice.techniques)
        nice_scores.append(s)
        per_criterion["technique (nice)"] = s
        explanations["technique (nice)"] = expl

    if nice.min_samples is not None:
        actual = candidate.sample_count or 0
        if actual >= nice.min_samples:
            s = min(1.0, actual / (nice.min_samples * 2))
        else:
            s = actual / nice.min_samples if nice.min_samples > 0 else 0.0
        nice_scores.append(s)
        per_criterion["min_samples (nice)"] = s
        explanations["min_samples (nice)"] = f"{actual} samples (target ≥{nice.min_samples})"

    if nice.has_raw_data is True:
        raw = candidate.raw_file_count or 0
        s = 1.0 if raw > 0 else 0.0
        nice_scores.append(s)
        per_criterion["raw_data (nice)"] = s
        explanations["raw_data (nice)"] = f"{raw} raw files"

    if nice.ionization_modes:
        s, expl = _score_ionization(candidate, nice.ionization_modes)
        nice_scores.append(s)
        per_criterion["ionization (nice)"] = s
        explanations["ionization (nice)"] = expl

    if nice.analysis_types:
        s, expl = _score_design_descriptors(candidate, nice.analysis_types)
        nice_scores.append(s)
        per_criterion["analysis_type (nice)"] = s
        explanations["analysis_type (nice)"] = expl

    if nice.data_formats:
        s, expl = _score_data_formats(candidate, nice.data_formats)
        nice_scores.append(s)
        per_criterion["data_format (nice)"] = s
        explanations["data_format (nice)"] = expl

    if nice.has_maf is True:
        has_maf = candidate.maf_files_parsed and (candidate.metabolite_count or 0) > 0
        s = 1.0 if has_maf else 0.0
        nice_scores.append(s)
        per_criterion["maf (nice)"] = s
        explanations["maf (nice)"] = (
            f"{candidate.metabolite_count} metabolites in MAF"
            if has_maf else "no MAF / metabolite count"
        )

    if nice.min_metabolites is not None:
        actual = candidate.metabolite_count or 0
        if actual >= nice.min_metabolites:
            s = min(1.0, actual / (nice.min_metabolites * 2))
        else:
            s = actual / nice.min_metabolites if nice.min_metabolites > 0 else 0.0
        nice_scores.append(s)
        per_criterion["min_metabolites (nice)"] = s
        explanations["min_metabolites (nice)"] = f"{actual} metabolites (target ≥{nice.min_metabolites})"

    # Free-text relevance via description word overlap
    if profile.free_text:
        s, expl = _score_text_relevance(candidate, profile.free_text)
        nice_scores.append(s)
        per_criterion["text_relevance"] = s
        explanations["text_relevance"] = expl

    # Metadata completeness bonus
    completeness = candidate.metadata_completeness
    per_criterion["metadata_completeness"] = completeness
    explanations["metadata_completeness"] = f"{completeness:.0%} metadata fields populated"
    nice_scores.append(completeness * 0.5)  # weight at 50%

    # Combine nice-to-have scores
    if nice_scores:
        score.overall = sum(nice_scores) / len(nice_scores)
    else:
        # If no nice-to-haves, score by completeness
        score.overall = completeness

    # If hard failed, clamp overall
    if not score.hard_passed:
        score.overall = score.overall * 0.25  # still show some signal but heavily discounted

    score.per_criterion = per_criterion
    score.criterion_explanations = explanations
    return score


# ── Helper functions ──


def _term_names(
    terms: list[Any],
) -> list[str]:
    """Extract term names from OntologyTerm objects or dicts."""
    names = []
    for t in terms:
        if hasattr(t, "term"):
            names.append(t.term)
        elif isinstance(t, dict):
            names.append(t.get("term", ""))
    return [n for n in names if n]


def _check_terms(
    candidate_terms: list[Any], required: list[str], mode: str = "any"
) -> tuple[bool, str]:
    """Check if candidate has at least one (any) or all of the required terms.

    Matching is SUBSTRING-based (case-insensitive ``req in name``): the term
    must appear inside the candidate's value verbatim.  This is why the
    profile must use each repository's canonical vocabulary — e.g. Workbench
    organisms are Latin names (``Homo sapiens``, ``"Human"`` never matches),
    and MetaboLights sample types are the ``organismParts.term`` facets
    (``"blood plasma"``, not ``"Serum"``).  The screen therefore also CANNOT
    decide serum-vs-plasma from the search index (the facet value is a single
    string); that distinction is per-sample, deep-only.
    """
    names = [t.lower() for t in _term_names(candidate_terms)]
    required_lower = [r.lower() for r in required]

    if mode == "any":
        found = any(req in n for n in names for req in required_lower)
        matched = [r for r in required_lower if any(r in n for n in names)]
        if found:
            return True, f"matched: {matched}"
        return False, f"not found (have: {names})"
    else:
        all_found = all(
            any(req in n for n in names) for req in required_lower
        )
        missing = [r for r in required_lower if not any(r in n for n in names)]
        if all_found:
            return True, "all matched"
        return False, f"missing: {missing}"


def _score_terms(
    candidate_terms: list[Any], desired: list[str]
) -> tuple[float, str]:
    """Score how well candidate terms match desired terms (0-1)."""
    names = [t.lower() for t in _term_names(candidate_terms)]
    desired_lower = [d.lower() for d in desired]

    if not names or not desired_lower:
        return 0.0, "no terms to compare"

    matches = sum(
        1 for d in desired_lower if any(d in n for n in names)
    )
    score = matches / len(desired_lower)
    matched_terms = [d for d in desired if d.lower() in [n for n in names]]
    return score, f"{' + '.join(matched_terms) if matched_terms else 'no direct match'}"


def _check_techniques(
    candidate: StudyCandidate, required: list[str]
) -> tuple[bool, str]:
    """Check if any assay technique matches required."""
    techs = _get_technique_names(candidate)
    required_lower = [r.lower() for r in required]
    found = any(any(r in t.lower() for r in required_lower) for t in techs)
    if found:
        return True, f"techniques: {techs}"
    return False, f"techniques found: {techs}"


def _score_techniques(
    candidate: StudyCandidate, desired: list[str]
) -> tuple[float, str]:
    """Score technique match (0-1)."""
    techs = _get_technique_names(candidate)
    if not techs:
        return 0.0, "no techniques listed"

    desired_lower = [d.lower() for d in desired]
    matches = sum(
        1 for d in desired_lower if any(d in t.lower() for t in techs)
    )
    score = matches / len(desired_lower) if desired_lower else 0.0
    return score, f"techniques: {techs}"


def _get_technique_names(candidate: StudyCandidate) -> list[str]:
    """Get all technique names from assays and assay_techniques."""
    names = []
    for at in candidate.assay_techniques:
        if isinstance(at, dict):
            names.append(at.get("name", ""))
            names.append(at.get("technique", ""))
        elif hasattr(at, "name"):
            names.append(at.name)
    for a in candidate.assays:
        if a.technique_name:
            names.append(a.technique_name)
    return [n for n in names if n]


def _check_ionization(
    candidate: StudyCandidate, modes: list[str]
) -> tuple[bool, str]:
    """Check ionization mode match."""
    found_modes = _get_ionization_modes(candidate)
    modes_lower = [m.lower() for m in modes]
    found = any(
        any(m in fm.lower() for m in modes_lower) for fm in found_modes
    )
    return found, f"ionization modes: {found_modes or 'unknown'}"


def _score_ionization(
    candidate: StudyCandidate, modes: list[str]
) -> tuple[float, str]:
    """Score ionization mode match (0-1)."""
    found_modes = _get_ionization_modes(candidate)
    if not found_modes:
        return 0.0, "ionization mode not specified in metadata"
    modes_lower = [m.lower() for m in modes]
    matches = sum(
        1 for m in modes_lower if any(m in fm.lower() for fm in found_modes)
    )
    score = min(1.0, matches / len(modes))
    return score, f"found: {found_modes}"


def _get_ionization_modes(candidate: StudyCandidate) -> list[str]:
    """Extract ionization modes from assays and description."""
    modes = set()
    for a in candidate.assays:
        if a.ionization_mode:
            modes.add(a.ionization_mode)
    # Also check description
    desc = (candidate.description or "").lower()
    if "positive" in desc:
        modes.add("positive")
    if "negative" in desc:
        modes.add("negative")
    return list(modes)


def _check_design_descriptors(
    candidate: StudyCandidate, types: list[str]
) -> tuple[bool, str]:
    """Check analysis type / design descriptor match."""
    descs = [d.term for d in candidate.design_descriptors]
    types_lower = [t.lower() for t in types]
    found = any(any(t in d.lower() for t in types_lower) for d in descs)
    return found, f"design descriptors: {descs}"


def _score_design_descriptors(
    candidate: StudyCandidate, types: list[str]
) -> tuple[float, str]:
    """Score design descriptor match."""
    descs = [d.term for d in candidate.design_descriptors]
    if not descs:
        return 0.0, "no design descriptors"
    types_lower = [t.lower() for t in types]
    matches = sum(1 for t in types_lower if any(t in d.lower() for d in descs))
    score = matches / len(types_lower) if types_lower else 0.0
    return score, f"descriptors: {descs}"


def _check_data_formats(
    candidate: StudyCandidate, formats: list[str]
) -> tuple[bool, str]:
    """Check if study has data in requested formats."""
    # This is best-effort from Phase 1 data; Phase 2 data files would be more accurate
    desc = (candidate.description or "").lower()
    formats_lower = [f.lower() for f in formats]
    found = any(
        any(fmt in desc for fmt in formats_lower) for _ in [1]
    )
    # Also check file counts
    has_raw = (candidate.raw_file_count or 0) > 0
    return found or has_raw, f"raw files: {candidate.raw_file_count}"


def _score_data_formats(
    candidate: StudyCandidate, formats: list[str]
) -> tuple[float, str]:
    """Score data format match."""
    if (candidate.raw_file_count or 0) > 0:
        return 0.8, f"{candidate.raw_file_count} raw files available"
    return 0.0, "no raw files found"


def _score_text_relevance(
    candidate: StudyCandidate, free_text: str
) -> tuple[float, str]:
    """Simple word-overlap relevance scoring for free text."""
    if not free_text:
        return 0.0, ""

    query_words = set(re.findall(r"[a-zA-Z]+", free_text.lower()))
    query_words -= {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "can", "shall", "need",
        "i", "we", "you", "they", "he", "she", "it", "this", "that",
        "of", "in", "on", "at", "to", "for", "with", "by", "from",
        "and", "or", "but", "not", "no", "so", "if", "then", "than",
        "also", "very", "just", "about", "into", "over", "between",
        "data", "study", "studies", "using", "based",
    }

    if not query_words:
        return 0.5, "no significant query terms"

    text = f"{candidate.title} {candidate.description}".lower()
    matches = sum(1 for w in query_words if w in text)
    score = min(1.0, matches / len(query_words) * 1.5)
    return score, f"{matches}/{len(query_words)} query terms matched"