"""Canonical workbench vocabularies — the controlled lists.

The workbench's metstat slots and client-side screening use controlled
values derived from the whole-index maps (the same pools its own pulldowns
read).  Distinct-value counts (pinned by tests): **disease 259, source 328,
species 506** (species = latin+common pairs).

A ``Vocab`` is derived from a ``WorkbenchCorpus`` at runtime, or loaded from
vendored snapshots (``tests/fixtures/workbench/vocab_*.json``) for tests:
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from metabo_search.repositories.workbench.corpora import WorkbenchCorpus


@dataclass
class Vocab:
    """Canonical controlled lists with mappings the matcher needs.

    Attributes
    ----------
    diseases : set[str]
        Canonical disease values (e.g. ``Alzheimers disease``).
    sources : set[str]
        Canonical sample-source values (Blood, Cultured cells, …).
    species_common : set[str]
        Common species names (Human, Mouse, Rat) — what metstat accepts.
    latin_to_common : dict[str, str]
        ``Homo sapiens`` → ``Human`` (organism mapping to metstat).
    studies_per_value : dict[str, int]
        Per disease/source value: how many studies use it (evidence counts
        for ambiguity notices).
    """

    diseases: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    species_common: set[str] = field(default_factory=set)
    latin_to_common: dict[str, str] = field(default_factory=dict)
    studies_per_value: dict[str, int] = field(default_factory=dict)

    # ── derivation (pure) ───────────────────────────────────────────

    @classmethod
    def from_corpus(cls, corpus: WorkbenchCorpus) -> Vocab:
        diseases: dict[str, int] = {}
        sources: dict[str, int] = {}
        for vals in corpus.disease.values():
            for v in vals:
                diseases[v] = diseases.get(v, 0) + 1
        for vals in corpus.source.values():
            for v in vals:
                sources[v] = sources.get(v, 0) + 1
        counts = {**diseases, **sources}
        return cls(
            diseases=set(diseases),
            sources=set(sources),
            species_common=set(corpus.species_common),
            latin_to_common=dict(corpus.species_latin),
            studies_per_value=counts,
        )

    # ── fixtures (tests) ────────────────────────────────────────────

    @classmethod
    def from_fixtures(cls, dir_path: Path) -> Vocab:
        """Load vocab snapshots (pinned counts) from a fixtures dir."""
        def values(name: str, key: str = "values"):
            d = json.loads((dir_path / name).read_text())
            return d[key]
        species = values("vocab_species.json")           # [{latin, common}]
        latin = {p["latin"]: p["common"] for p in species}
        commons = {p["common"] for p in species}
        return cls(
            diseases=set(values("vocab_disease.json")),
            sources=set(values("vocab_source.json")),
            species_common=commons,
            latin_to_common=latin,
            studies_per_value={},
        )