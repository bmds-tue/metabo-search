"""Workbench search — corpus backstop + metstat fast path → StudyCandidate.

Search is deterministic: when ``metstat`` is not confidently usable (or its
slots are all empty), matching happens *locally* over the cached whole-index
corpus by free-text term overlap — the analogue of MetaboLights' Solr query.
The fast path (metstat) can only *narrow* the corpus result with confident
slot values; it is never the sole path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from metabo_search.models import OntologyTerm, StudyCandidate
from metabo_search.repositories.workbench.corpora import WorkbenchCorpus
from metabo_search.repositories.workbench.matcher import MatchResult
from metabo_search.repositories.workbench.slots import SlotAssembly


def _tokens(s: str) -> set[str]:
    """Lowercased word tokens (stopword-free-ish; keeps numbers)."""
    return set(re.findall(r"[a-z0-9']+", s.lower()))


STOPWORDS = {
    "the", "a", "an", "in", "of", "with", "and", "or", "for", "on", "by",
    "to", "from", "patients", "study", "studies", "using", "metabolomics",
    "metabolomic", "profiling", "analysis", "human",
}
"""Tokens ignored for term-overlap scoring (noise, not signal)."""


@dataclass
class CorpusMatch:
    """One corpus study's relevance verdict by free-text term overlap.

    Attributes
    ----------
    study_id : str
    score : float
        Fraction of query tokens the title contains (0-1).
    matched_tokens : list[str]
        Which query tokens hit (transparency for the agent).
    source_terms : list[str]
        Sources this study carries (disease terms) for screen enrichment.
    """

    study_id: str
    score: float
    matched_tokens: list[str] = field(default_factory=list)
    source_terms: list[str] = field(default_factory=list)


def corpus_rank(
    query_tokens: set[str],
    corpus: WorkbenchCorpus,
    *,
    min_overlap: float = 0.0,
    only_ids: set[str] | None = None,
) -> list[CorpusMatch]:
    """Rank studies by query-token coverage of their titles.

    Parameters
    ----------
    query_tokens : set[str]
        Free-text tokens (stopwords removed internally).
    corpus : WorkbenchCorpus
        The whole-index corpus.
    min_overlap : float
        Minimum fractional overlap; studies below are dropped (only
        meaningful when ``only_ids`` is unset — pool mode keeps everything).
    only_ids : set[str] | None
        Restrict ranking to these study ids (e.g. a metstat profile pool).
        Studies without any token overlap are still included, scored 0, so a
        structured profile is never starved by title wording.

    Returns
    -------
    list[CorpusMatch]
        Deterministic: sorted by ``(-score, study_id)``.
    """
    q = query_tokens - STOPWORDS
    if only_ids is None:
        ids = list(corpus.summaries)
    else:
        ids = [sid for sid in only_ids if sid in corpus.summaries]
    matches: list[CorpusMatch] = []
    for sid in ids:
        s = corpus.summaries.get(sid, {})
        t = _tokens(s.get("study_title", ""))
        hit = q & t if q else set()
        score = len(hit) / len(q) if q else 0.0
        if only_ids is None and (not hit or score < min_overlap):
            continue
        matches.append(CorpusMatch(
            study_id=sid, score=score, matched_tokens=sorted(hit),
            source_terms=corpus.disease.get(sid, [])))
    matches.sort(key=lambda m: (-m.score, m.study_id))
    return matches


def metstat_matches(slots: tuple[str, ...]) -> set[str]:
    """Which studies the server-side metstat slots return (the profile pool).

    Confident slots encode the structured profile (species/source/disease),
    so this is the *candidate pool*; free-text ranking happens afterwards,
    not as an intersection (a title never contains every free-text word).
    """
    from metabo_search.repositories.workbench.client import metstat
    rows = metstat(slots)
    return {row.get("study") for row in rows.values()
            if row.get("study")}


def build_candidates(
    matches: list[CorpusMatch],
    corpus: WorkbenchCorpus,
    *,
    max_results: int,
) -> list[StudyCandidate]:
    """Convert ranked corpus matches into shallow ``StudyCandidate``s."""
    out: list[StudyCandidate] = []
    for m in matches[:max_results]:
        s = corpus.summaries.get(m.study_id, {})
        species = s.get("species", "")
        descriptors = [OntologyTerm(term=d) for d in (m.source_terms or [])]
        analysis_type = s.get("analysis_type", "")
        if analysis_type:
            descriptors.append(OntologyTerm(term=analysis_type))
        sample_parts = [OntologyTerm(term=x)
                        for x in corpus.source.get(m.study_id, [])]
        out.append(StudyCandidate(
            study_id=m.study_id,
            title=s.get("study_title", ""),
            description=s.get("study_url", ""),
            status="public",
            repository="metabolomics_workbench",
            organisms=[OntologyTerm(term=s) for s in [species] if s],
            organism_parts=sample_parts,
            design_descriptors=descriptors,
            sample_count=_int_or_none(s.get("number_of_samples")),
            assay_techniques=[{"name": s.get("analysis_type")}]
            if s.get("analysis_type") else [],
            submission_date=s.get("submission_date", ""),
            public_release_date=s.get("release_date", ""),
        ))
    return out


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def screen_by_organisms(
    matches: list[CorpusMatch],
    organisms: list[str],
    corpus: WorkbenchCorpus,
    vocab_latin_to_common: dict[str, str],
    vocab_commons: set[str],
) -> list[CorpusMatch]:
    """Filter corpus matches to studies whose species is one of ``organisms``.

    Handles Latin and common spellings via ``vocab``.  If none of the
    requested organisms resolve to a known common name, the list is passed
    through unchanged (a failed resolution must not drop everything).
    """
    from metabo_search.repositories.workbench.matcher import map_latin_to_common
    commons = {map_latin_to_common(o, vocab_latin_to_common, vocab_commons)
               for o in organisms}
    commons = {c for c in commons if c}
    if not commons:
        return matches
    allowed = set().union(*(corpus.species_common.get(c, set())
                           for c in commons))
    if not allowed:
        return matches
    return [m for m in matches if m.study_id in allowed]


# ── the search entry point ──────────────────────────────────────────


def search_workbench(
    query: str,
    *,
    profile: Any = None,
    corpus: WorkbenchCorpus,
    vocab: Any,
    assembly: SlotAssembly | None = None,
    max_results: int = 200,
    min_samples: int | None = None,
) -> tuple[list[StudyCandidate], dict[str, Any]]:
    """Deterministic workbench search → (candidates, search_meta).

    Parameters
    ----------
    query : str
        Free-text query (used for term overlap + free-text disease source).
    profile : RequirementProfile | None
        Structured profile (drives metstat slots + min_samples).
    corpus : WorkbenchCorpus
        The cached whole-index corpus.
    vocab : Vocab
        Canonical lists for slot assembly.
    assembly : SlotAssembly | None
        Precomputed slot verdicts (built here when ``None``).
    max_results : int
        Candidate cap (post-filter).
    min_samples : int | None
        Hard filter on sample count (from the shallow summary).

    Returns
    -------
    (list[StudyCandidate], dict)
        Candidates + search meta: ``{"slots": …, "decisions": …,
        "fast_path": bool, "notice": str | None, "matched": n}``.
    """
    if assembly is None:
        from metabo_search.repositories.workbench.slots import assemble_slots
        assembly = assemble_slots(profile, vocab)

    q_tokens = _tokens(query)
    if profile and profile.free_text:
        q_tokens |= _tokens(profile.free_text)

    if assembly.used_fast_path:
        # structured profile → server-side pool; free text only RANKS it
        # (never starves it: pool studies with no token overlap stay, score 0)
        pool = metstat_matches(assembly.slots)
        ranked = corpus_rank(q_tokens, corpus, only_ids=pool)
    else:
        # no confident slots → pure free-text term match (the backstop);
        # profile organisms still constrain the view
        ranked = corpus_rank(q_tokens, corpus)
        if profile is not None and (profile.hard.organisms
                                    if profile.hard else None):
            ranked = screen_by_organisms(
                ranked, list(profile.hard.organisms), corpus,
                vocab.latin_to_common, vocab.species_common)

    cands = build_candidates(ranked, corpus, max_results=max_results)
    if min_samples is not None:
        cands = [c for c in cands
                 if (c.sample_count or 0) >= min_samples]

    notice = build_notice(assembly.decisions)
    meta: dict[str, Any] = {
        "slots": list(assembly.slots),
        "decisions": {k: v.fmt() for k, v in assembly.decisions.items()},
        "fast_path": assembly.used_fast_path,
        "notice": notice,
        "matched": len(cands),
    }
    return cands, meta


def build_notice(decisions: dict[str, MatchResult]) -> str | None:
    """Actionable ambiguity notice (the agent's instructions), or None.

    When any slot decision abstained (ambiguous), produce a recipe-shaped
    text: what was ambiguous, the candidates with counts, and exactly which
    call to make next (refined free text + workbench-only re-run + input=
    composition).  Nothing — no notice — when every decision is confident
    or absent.
    """
    blocked = {name: mr for name, mr in decisions.items()
               if mr.term and mr.value is None}
    if not blocked:
        return None
    parts = []
    for name, mr in blocked.items():
        alts = ", ".join(
            f"{v} ({mr.study_count if hasattr(mr, 'study_count') and mr.study_count else '?'})"
            for v, _ in mr.alternates) or "no close candidates"
        parts.append(f"{name} term {mr.term!r} ambiguous — none entered "
                     f"(candidates: {alts})")
    lead = "; ".join(parts)
    return (
        f"workbench vocabulary notice: {lead}. Results below are "
        f"corpus-term-matched. To enable server-side slot filtering, refine "
        f"the free text (e.g. a canonical disease name) and re-run with "
        f"databases=('metabolomics_workbench',), then combine via "
        f"pipeline(input=SearchResult(...))."
    )