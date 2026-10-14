"""Live corpus-cache promise: a warm cache means zero requests.

The whole-index fetch is ~90 s, so this lives in the ``live_slow`` tier (weekly).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from metabo_search import Workbench

pytestmark = [pytest.mark.live, pytest.mark.live_slow]


def test_corpus_fetch_then_cache_hit(tmp_path: Path) -> None:
    with Workbench(cache_dir=tmp_path) as first:
        corpus = first.all_studies()
        assert len(corpus) > 1000, "corpus looks truncated"
        assert first.stats.requests >= 1

    with Workbench(cache_dir=tmp_path) as second:
        cached = second.all_studies()
        assert cached.keys() == corpus.keys()
        assert second.stats.requests == 0, "a warm cache must not hit /ST/summary"
