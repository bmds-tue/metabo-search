"""The workbench whole-index corpus — download once, filter locally forever.

The REST API has no free-text search endpoint; its ``ST/summary`` corpus
(all studies) plus the disease/source/species maps provide everything needed
to screen locally.  Corpus layout per cache root::

    <root>/workbench/corpus.json      {summary corpus + maps, one JSON blob}
    <root>/workbench/STAMP             ISO created_at (TTL check)

First fetch is slow (~90 s, unavoidable); afterwards every search/filter is
CPU-only over the cached blob.  TTL: 7 days (mirrors the search-index TTL).

Fixture mode: when no cache root is given AND ``METABO_WORKBENCH_FIXTURES``
points at a fixtures dir, the corpus is loaded from JSON files there instead
of the network — that is how all offline tests run.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from metabo_search.repositories.workbench import client

CORPUS_TTL_DAYS = 7


@dataclass
class WorkbenchCorpus:
    """The normalized whole-index snapshot used for local screening.

    Attributes
    ----------
    summaries : dict[str, dict]
        study_id → summary record (title, species, analysis_type, …).
    disease : dict[str, list[str]]
        study_id → disease terms (possibly several).
    source : dict[str, list[str]]
        study_id → sample-source terms.
    species_common : dict[str, set[str]]
        common name → set of study ids (for species screening).
    species_latin : dict[str, str]
        latin name → common name (organism mapping, metstat needs common).

    All lookups are by workbench study id (ST…).
    """

    summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    disease: dict[str, list[str]] = field(default_factory=dict)
    source: dict[str, list[str]] = field(default_factory=dict)
    species_common: dict[str, set[str]] = field(default_factory=dict)
    species_latin: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.species_common = {
            k: set(v) for k, v in self.species_common.items()}

    @property
    def study_ids(self) -> list[str]:
        return list(self.summaries)

    def to_json(self) -> str:
        return json.dumps({
            "summaries": self.summaries,
            "disease": self.disease,
            "source": self.source,
            "species_common": {k: sorted(v)
                               for k, v in self.species_common.items()},
            "species_latin": self.species_latin,
        }, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> WorkbenchCorpus:
        d = json.loads(s)
        return cls(summaries=d["summaries"], disease=d["disease"],
                   source=d["source"],
                   species_common=d.get("species_common", {}),
                   species_latin=d.get("species_latin", {}))


# ── normalization (pure, testable) ─────────────────────────────────


def _row_values(payload: dict[str, dict], key: str) -> dict[str, list[str]]:
    """Group a flat ``study_id → {.., key: val}`` map per study (multi-valued)."""
    out: dict[str, list[str]] = {}
    for row in payload.values():
        sid = row.get("Study ID")
        if sid:
            out.setdefault(sid, [])
            if row.get(key):
                out[sid].append(row[key])
    return out


def build_corpus(
    summaries: dict[str, dict[str, str]],
    disease: dict[str, dict[str, str]],
    source: dict[str, dict[str, str]],
    species: dict[str, dict[str, str]],
) -> WorkbenchCorpus:
    """Build the normalized corpus from raw API payloads (pure, no I/O)."""
    c = WorkbenchCorpus(
        summaries={row.get("study_id", k): row for k, row in
                   summaries.items() if row.get("study_id")},
        disease={sid: list(set(v)) for sid, v in
                 _row_values(disease, "Disease").items()},
        source={sid: list(set(v)) for sid, v in
                _row_values(source, "Sample source").items()},
        species_latin={row.get("Latin name", ""): row.get("Common name", "")
                       for row in species.values()
                       if row.get("Latin name")},
    )
    for sid, row in species.items():
        common = row.get("Common name")
        latin = row.get("Latin name")
        # The REST response keys the rows by row index ('1', '2', …); the
        # canonical study id lives in the "Study ID" field.  species_common
        # must key BY STUDY ID so screening joins with ``summaries`` (which
        # are keyed by study id) — otherwise every organism screen matches
        # NOTHING (verified against the live corpus: 0 of 1891 intersect).
        real_id = row.get("Study ID") or sid
        if common:
            c.species_common.setdefault(common, set()).add(real_id)
        elif latin:
            c.species_common.setdefault(latin, set()).add(real_id)
    return c


# ── fetch + cache ──────────────────────────────────────────────────


def corpus_path(root: str | Path) -> Path:
    p = Path(root) / "workbench"
    p.mkdir(parents=True, exist_ok=True)
    return p / "corpus.json"


def load_corpus(cache_root: str | Path | None) -> WorkbenchCorpus:
    """Load the corpus, fetching + caching it when missing or stale.

    Order of resolution (offline-first):
    1. ``METABO_WORKBENCH_FIXTURES`` env → load from fixture files (tests).
    2. `<cache_root>/workbench/corpus.json` if fresh (TTL 7 d) → load.
    3. otherwise fetch from the live API, normalize, store.

    Raises ``RuntimeError`` if fixtures are requested but incomplete.
    """
    fixtures = os.environ.get("METABO_WORKBENCH_FIXTURES")
    if fixtures:
        return load_corpus_fixtures(Path(fixtures))
    if cache_root is None:
        raise ValueError(
            "workbench search needs a cache root (pipeline.cache(...)) "
            "or METABO_WORKBENCH_FIXTURES for offline tests")
    root = Path(cache_root)
    path = corpus_path(root)

    stamp = root / "workbench" / "STAMP"
    if path.exists() and stamp.exists():
        try:
            age = time.time() - float(stamp.read_text().strip())
            if age < CORPUS_TTL_DAYS * 86400:
                return WorkbenchCorpus.from_json(path.read_text())
        except ValueError:
            pass  # corrupt stamp → refetch

    # the summary corpus call is slow (~90 s) and occasionally times out
    # server-side; retry a couple of times before giving up.
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            corpus = build_corpus(client.all_studies_summary(),
                                  client.all_disease_map(),
                                  client.all_source_map(),
                                  client.all_species_map())
            break
        except Exception as e:  # noqa: BLE001 — retry any fetch failure
            last_err = e
            time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"workbench corpus fetch failed: {last_err}")
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(corpus.to_json())
    stamp.write_text(str(time.time()))
    return corpus


def load_corpus_fixtures(dir_path: Path) -> WorkbenchCorpus:
    """Load a corpus from JSON fixtures (offline tests).

    Expects ``summary_corpus.json``, ``disease_map.json``, ``source_map.json``,
    ``species_map.json`` in ``dir_path``.
    """
    def read(name: str) -> dict:
        p = dir_path / name
        if not p.exists():
            raise RuntimeError(f"missing workbench fixture {p}")
        return json.loads(p.read_text())

    return build_corpus(read("summary_corpus.json"), read("disease_map.json"),
                        read("source_map.json"), read("species_map.json"))