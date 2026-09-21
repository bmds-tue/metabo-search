"""MetStat slot assembly — turn a profile into confident slot values.

v1 policy (see plan §3): fill **SPECIES / SOURCE / DISEASE** only.  Each slot
is filled from its profile field via :func:`matcher.match` *and only when
the matcher is confident*; anything else stays an empty wildcard, in which
case the corpus backstop does the filtering locally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from metabo_search.repositories.workbench.matcher import (
    MatchResult,
    map_latin_to_common,
    match,
)
from metabo_search.repositories.workbench.vocab import Vocab

SLOT_COUNT = 8  # ANALYSIS;POLARITY;CHROM;SPECIES;SOURCE;DISEASE;KEGG;REFMET


@dataclass
class SlotAssembly:
    """The metstat slot verdict for a profile.

    Attributes
    ----------
    slots : tuple[str, ...]
        Length-8 metstat tuple; empty strings are wildcards.
    decisions : dict[str, MatchResult]
        Per slot name the matcher verdicts used (for notices/auditing).
    used_fast_path : bool
        True when at least one slot is filled (metstat call justified).
    """

    slots: tuple[str, ...] = ("",) * SLOT_COUNT
    decisions: dict[str, MatchResult] = field(default_factory=dict)
    used_fast_path: bool = False


def assemble_slots(profile: Any, vocab: Vocab) -> SlotAssembly:
    """Build metstat slots from a profile + corpus vocab (pure, no I/O).

    Rules (plan §3/§7): slots are filled ONLY from structured profile
    fields, and only on a confident matcher verdict.  Free text is NEVER a
    slot term — arbitrary sentences false-positive onto disease names
    ("serum metabolomics"→"Metabolic syndrome"); free text is handled by
    the corpus term-match backstop instead.

    Parameters
    ----------
    profile : RequirementProfile | None
        Structured profile.  Fields consulted: ``hard/… .organisms``
        (→ SPECIES), ``.sample_types`` (→ SOURCE), ``.diseases`` (→ DISEASE).
    vocab : Vocab
        Canonical lists + counts.

    Returns
    -------
    SlotAssembly
        Confident fills only; every slot defaulting to a wildcard keeps the
        fast path honest.
    """
    hard = profile.hard if profile is not None else None

    candidates: dict[str, list[tuple[str, MatchResult]]] = {}
    if hard is not None:
        for org in hard.organisms or []:
            common = map_latin_to_common(org, vocab.latin_to_common,
                                         vocab.species_common)
            if common:
                candidates.setdefault("species", []).append((
                    common, MatchResult(term=org, value=common,
                                        score=100.0, margin=100.0)))
        for st in hard.sample_types or []:
            candidates.setdefault("source", []).append(
                (st, match(st, vocab.sources,
                           study_counts=vocab.studies_per_value)))
    # disease: structured field only (free text handled by the corpus path)
    for dt in (hard.diseases or []) if hard is not None else []:
        candidates.setdefault("disease", []).append(
            (dt, match(dt, vocab.diseases,
                       study_counts=vocab.studies_per_value)))

    slots: list[str] = [""] * SLOT_COUNT
    decisions: dict[str, MatchResult] = {}
    used = False
    # slot positions in the metstat tuple
    POS = {"species": 3, "source": 4, "disease": 5}
    for slot_name in ("species", "source", "disease"):
        entries = candidates.get(slot_name, [])
        if not entries:
            continue
        # prefer the first confident entry; else the best always
        best = next((e for e in entries if e[1].confident), None)
        if best is None:
            best = max(entries, key=lambda e: e[1].score, default=None)
        if best is None:
            continue
        _, mr = best                    # keep the match result only
        if mr.confident:
            slots[POS[slot_name]] = mr.value
            used = True
        decisions[slot_name] = mr
    return SlotAssembly(slots=tuple(slots), decisions=decisions,
                        used_fast_path=used)