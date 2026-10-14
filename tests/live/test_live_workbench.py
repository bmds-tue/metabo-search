"""Live Workbench conformance: request counts, errors, and empty endpoints."""

from __future__ import annotations

import pytest
from anchors import WB_ANCHORS, WB_EMPTY_METABOLITES, WB_MISSING

from metabo_search import Workbench
from metabo_search.errors import MetaboSearchError, NotFound

pytestmark = pytest.mark.live


def test_anchor_lookups_resolve(wb: Workbench) -> None:
    for study_id in WB_ANCHORS:
        try:
            summary = wb.study(study_id)
        except NotFound:
            pytest.fail(f"live anchor {study_id} disappeared upstream")
        except MetaboSearchError as exc:
            pytest.skip(f"{study_id}: {exc}")
        assert summary.study_id == study_id
        assert summary.study_title


def test_inspect_collects_errors(wb: Workbench) -> None:
    details = wb.inspect([WB_ANCHORS[0], WB_MISSING], workers=1)
    assert [d.study_id for d in details] == [WB_ANCHORS[0], WB_MISSING]
    good, missing = details
    assert not good.error and good.samples
    assert missing.error


def test_samples_only_uses_one_request_for_a_passed_summary(wb: Workbench) -> None:
    summary = wb.study(WB_ANCHORS[0])  # fetched before the snapshot on purpose
    before = wb.stats
    detail = wb.inspect(
        [summary], workers=1, include_analyses=False, include_metabolites=False
    )[0]
    delta = wb.stats - before
    assert delta.requests == 1, "a passed summary + samples only is one request"
    assert detail.samples
    assert detail.analyses == [] and detail.metabolites == []


def test_full_inspect_uses_three_requests_for_a_passed_summary(wb: Workbench) -> None:
    summary = wb.study(WB_ANCHORS[0])
    before = wb.stats
    detail = wb.inspect([summary], workers=1)[0]
    delta = wb.stats - before
    assert delta.requests == 3, "passed summary: factors + analysis + metabolites"
    assert detail.summary is summary
    assert detail.samples and detail.analyses and detail.metabolites


def test_empty_metabolites_means_not_listed(wb: Workbench) -> None:
    assert wb.metabolites(WB_EMPTY_METABOLITES) == []


def test_missing_study_is_not_found(wb: Workbench) -> None:
    with pytest.raises(NotFound):
        wb.study(WB_MISSING)
