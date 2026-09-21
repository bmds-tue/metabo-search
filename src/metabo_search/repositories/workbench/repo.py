"""``WorkbenchRepository`` — the workbench behind the repository seam.

Subscribes to the pipeline via ``StudyRepository``; implements ``search``
now and adds deep inspection/sample/metabolite support incrementally.
"""

from __future__ import annotations

import os
from pathlib import Path

from metabo_search.repositories.base import StudyRepository
from metabo_search.repositories.workbench.corpora import load_corpus
from metabo_search.repositories.workbench.search import search_workbench
from metabo_search.repositories.workbench.vocab import Vocab

_FIXTURES_ENV = "METABO_WORKBENCH_FIXTURES"


class WorkbenchRepository(StudyRepository):
    """NIH Common Fund NMDR repository (ST… accession space)."""

    name = "metabolomics_workbench"

    # ── search ──────────────────────────────────────────────────────

    def search(self, query, *, profile=None, page_size=100, max_results=200,
               filters=None, sort_field=None, sort_direction="desc",
               ms_filters=None, organism=None, technique=None,
               sample_type=None, min_samples=None, min_raw_files=None,
               cache_root=None):
        """Search the workbench whole-index corpus (deterministic).

        Uses the cached corpus when ``cache_root`` is given, else the
        ``METABO_WORKBENCH_FIXTURES`` env for offline tests.  Without either
        it raises (the workbench cannot search without its index).
        """
        corpus = load_corpus(cache_root)
        vocab = Vocab.from_corpus(corpus)
        cands, meta = search_workbench(
            query, profile=profile, corpus=corpus, vocab=vocab,
            max_results=max_results, min_samples=min_samples)
        for c in cands:
            c.repository = self.name
        return cands, meta

    # ── deep ────────────────────────────────────────────────────────

    def deep_metadata(self, candidates, *, workers=10, tmp_dir=None,
                      parse_workers=None, cache_root=None):
        """Deep-inspect workbench candidates (summary/factors/analysis)."""
        from metabo_search.repositories.workbench.inspect import deep_metadata
        return deep_metadata(
            candidates, workers=workers,
            load_payload=_payload_loader())

    # ── sample rows / metabolites ───────────────────────────────────

    def sample_rows(self, candidate):
        from metabo_search.repositories.workbench.inspect import sample_rows
        return sample_rows(candidate)

    def metabolite_table(self, candidate):
        from metabo_search.repositories.workbench.datatable import datatable_rows
        return datatable_rows(candidate)


def _payload_loader():
    """Return a payload fetcher honoring fixtures env (offline tests).

    Returns a callable ``(study_id, kind) -> payload dict`` where kind is
    ``\"summary\" | \"factors\" | \"analysis\" | \"metabolites\"``.
    """
    fixtures = os.environ.get(_FIXTURES_ENV)
    if fixtures:
        base = Path(fixtures)
        import json

        def fixture_loader(study_id: str, kind: str) -> dict:
            p = base / f"study_{study_id}_{kind}.json"
            if not p.exists():
                raise FileNotFoundError(
                    f"missing workbench fixture {p} for {study_id}/{kind}")
            return json.loads(p.read_text())
        return fixture_loader

    from metabo_search.repositories.workbench import client

    def live_loader(study_id: str, kind: str) -> dict:
        fn = {
            "summary": client.study_summary_st,
            "factors": client.study_factors,
            "analysis": client.study_analysis,
            "metabolites": client.study_metabolites,
        }[kind]
        return fn(study_id)
    return live_loader