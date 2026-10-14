"""Live MetaboLights conformance: real payloads must honour the promises."""

from __future__ import annotations

import pytest
from anchors import ML_ANCHORS, ML_ERROR

from metabo_search import MetaboLights
from metabo_search.errors import MetaboSearchError, NotFound

pytestmark = pytest.mark.live


def test_anchor_lookups_resolve(ml: MetaboLights) -> None:
    for study_id in ML_ANCHORS:
        try:
            hit = ml.study(study_id)
        except NotFound:
            pytest.fail(f"live anchor {study_id} disappeared upstream")
        except MetaboSearchError as exc:  # transient upstream trouble
            pytest.skip(f"{study_id}: {exc}")
        assert hit.study_id == study_id
        assert hit.title


def test_search_respects_min_samples_and_is_unique(ml: MetaboLights) -> None:
    hits = ml.search("", min_samples=5, limit=10)
    assert hits, "search returned nothing"
    ids = hits.ids()
    assert len(ids) == len(set(ids)), "duplicate study ids in one search"
    assert all((hit.sample_count or 0) >= 5 for hit in hits), "min_samples not honoured"


def test_studies_preserve_input_order(ml: MetaboLights) -> None:
    ids = list(ML_ANCHORS)
    found = ml.studies(list(reversed(ids)))
    assert found.ids() == list(reversed(ids))


def test_inspect_is_ordered_and_best_effort(ml: MetaboLights) -> None:
    details = ml.inspect([ML_ANCHORS[0], ML_ERROR], workers=1)
    assert [d.study_id for d in details] == [ML_ANCHORS[0], ML_ERROR]
    good, broken = details
    assert not good.error and good.samples
    assert broken.error, "the broken-mirror anchor should report an error"
    assert not broken.samples and not broken.metabolites


def test_samples_only_skips_maf(ml: MetaboLights) -> None:
    detail = ml.inspect([ML_ANCHORS[0]], workers=1, include_metabolites=False)[0]
    assert detail.samples
    assert detail.metabolites == []
