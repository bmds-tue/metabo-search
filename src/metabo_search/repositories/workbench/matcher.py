"""Vocabulary matcher — map user phrasing onto canonical workbench values.

Engine: rapidfuzz (``WRatio``, ``score_cutoff``).  Our layer is only the
*decision*: normalize, alias-bridge the known semantic gaps, and decide by
`score AND margin` whether the mapping is confident enough to act on.

Contract
--------
- ``MatchResult.value is None`` ⇒ the caller must NOT act on the match
  (abstention).  The fast-path slot stays empty; screening falls back to
  the corpus term-match.  A failed match can never drop correct studies.
- An EXACT canonical value (normalized equality) always wins over fuzzy
  similarity: ``"cancer"`` → ``"Cancer"``, never the subtype ``"Lung
  cancer"`` (WRatio's partial-ratio boost would prefer the subtype), and
  ``"brain"`` → ``"Brain"`` (no Bee-Brain coin-flip).  Aliases resolve
  first.
- ``match()`` is deterministic (same inputs → same result); all
  thresholds are module constants so tests can pin behavior.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

# Decision thresholds (tuned against the real lists; see plan §7):
SCORE_CUTOFF = 75.0    # below this: no candidate is even considered.
                       #   Measured: legit matches live ≥78 ("alzheimers" 81,
                       #   "diabetes" 88, "urine" 80, …); off-topic sentences
                       #   land far below ("serum metabolomics" 63,
                       #   "gout"→"Micronutrient deficiency" 68).
TIE_SLACK = 2.0        # top-1 must beat top-2 by more than this: rejects
                       #   genuine coin-flips (Bee Brain/Brain 80/80,
                       #   Valley fever 90/0) where either choice is wrong.
TOP_N = 3              # alternates reported in MatchResult

# Semantic bridges string similarity cannot make ("plasma"→Blood etc.).
# Values MUST resolve onto a canonical list value (test-enforced).
ALIASES: dict[str, str] = {
    "blood plasma": "Blood",
    "plasma": "Blood",
    "serum": "Blood",
    "cell line": "Cultured cells",
    "cell lines": "Cultured cells",
    "cultured cell": "Cultured cells",
    "ipsc": "Cultured cells",
    "ipscs": "Cultured cells",
    "alzheimers": "Alzheimers disease",
    "alzheimer": "Alzheimers disease",
    "parkinsons": "Parkinsons disease",
    "parkinson": "Parkinsons disease",
    "type 2 diabetes": "Diabetes",
    "type ii diabetes": "Diabetes",
    "type 1 diabetes": "Diabetes",
    "diabetes mellitus": "Diabetes",
    "covid": "COVID-19",
    "covid-19": "COVID-19",
}


def _normalize(s: str) -> str:
    """Lowercase, drop apostrophes + periods, collapse whitespace."""
    s = s.lower().replace("'", "").replace(".", " ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


@dataclass
class MatchResult:
    """The matcher's verdict for one term against one vocabulary.

    Attributes
    ----------
    term : str
        The input phrase as given.
    value : str | None
        Chosen canonical value, or ``None`` = abstain.
    score : float
        Best similarity score (0-100).
    margin : float
        Score - second-best score.  ``None`` when only one candidate.
    alternates : list[tuple[str, float]]
        Runner-up candidates (canonical value, score) excluding the winner.
    study_count : int | None
        How many studies use the chosen value (evidence for the agent).
    by_alias : bool
        True when the decision came from the alias table.
    """

    term: str
    value: str | None = None
    score: float = 0.0
    margin: float | None = None
    alternates: list[tuple[str, float]] = field(default_factory=list)
    study_count: int | None = None
    by_alias: bool = False

    @property
    def confident(self) -> bool:
        """True when the value can be acted on (score + margin gates pass)."""
        return self.value is not None

    def fmt(self) -> str:
        if self.value is None:
            return (f"'{self.term}': no confident match (abstain; "
                    f"alternates {self.alternates})")
        return (f"'{self.term}' → {self.value} "
                f"(score {self.score:.0f}, margin "
                f"{self.margin:.1f} if not None else '-', n={self.study_count})")


def match(
    term: str,
    choices: Iterable[str],
    *,
    study_counts: dict[str, int] | None = None,
    alias: dict[str, str] | None = None,
) -> MatchResult:
    """Map one phrase onto the canonical value it most likely means.

    Parameters
    ----------
    term : str
        User phrasing (profile field value / free-text token).
    choices : Iterable[str]
        Canonical list to match against (from ``Vocab``).
    study_counts : dict[str, int] | None
        Per-canonical-value study counts for evidence.
    alias : dict[str, str] | None
        Extra alias overrides merged onto the built-in ``ALIASES``.

    Returns
    -------
    MatchResult
        ``value=None`` when the match is not confident (abstain).  Otherwise
        the winning canonical value + score/margin/alternates.
    """
    if alias:
        aliases = {**ALIASES, **alias}
    else:
        aliases = ALIASES
    norm = _normalize(term)
    matched_alias = None
    if not norm:
        return MatchResult(term=term)
    if norm in aliases:
        matched_alias = aliases[norm]
    elif norm in {_normalize(k) for k in aliases}:  # alias keys after norm
        for k, v in aliases.items():
            if _normalize(k) == norm:
                matched_alias = v
                break

    choices = sorted(choices)
    top = process.extract(term, choices, scorer=fuzz.WRatio, limit=TOP_N,
                          score_cutoff=SCORE_CUTOFF)
    if matched_alias is not None:
        if top and top[0][0] == matched_alias:
            pass  # alias agrees with similarity — keep the similarity verdict
        else:
            # alias overrides; report it as the decision with the similarity
            # score of the closest canonical value for transparency.
            alias_score = next((s for m, s, _ in top if m == matched_alias),
                               max((s for _, s, _ in top), default=0.0))
            return MatchResult(
                term=term, value=matched_alias, score=alias_score,
                margin=0.0, alternates=[(m, s) for m, s, _ in top],
                study_count=study_counts.get(matched_alias)
                if study_counts else None, by_alias=True)

    # Exact-value preference: when the normalized input IS a canonical value,
    # that value wins outright — never a fuzzy subtype.  Without this,
    # WRatio('cancer','Cancer') = 83.3 (case penalty) loses to
    # WRatio('cancer','Lung cancer') = 90 (partial-ratio boost), so a generic
    # term silently narrows to a subtype and the metstat pool starves every
    # other study of that class.
    exact_value = next((ch for ch in choices if _normalize(ch) == norm), None)
    if exact_value is not None:
        return MatchResult(
            term=term, value=exact_value, score=100.0, margin=100.0,
            alternates=[(m, s) for m, s, _ in top if m != exact_value],
            study_count=study_counts.get(exact_value)
            if study_counts else None)

    if not top:
        return MatchResult(term=term)
    best, best_score, _ = top[0]
    margin = (best_score - top[1][1]) if len(top) > 1 else best_score
    # extract already cut at SCORE_CUTOFF, so any winner is strong; the tie
    # slack only rejects true coin-flips where either pick would mislead.
    if margin <= TIE_SLACK:
        return MatchResult(term=term, score=best_score, margin=margin,
                          alternates=[(m, s) for m, s, _ in top])
    return MatchResult(
        term=term, value=best, score=best_score, margin=margin,
        alternates=[(m, s) for m, s, _ in top[1:]],
        study_count=study_counts.get(best) if study_counts else None)


def map_latin_to_common(organism: str, latin_to_common: dict[str, str],
                        commons: set[str]) -> str | None:
    """Map an organism string to a common species name (metstat requirement).

    Accepts a latin name exactly (``Rattus norvegicus`` → ``Rat``); anything
    already in the common-name set passes through; unknown → ``None`` (the
    caller will not fill the metstat SPECIES slot).
    """
    org = organism.strip()
    if org in commons:
        return org
    return latin_to_common.get(org)