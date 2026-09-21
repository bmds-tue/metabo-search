"""Recipes — opinionated mini-pipelines, same type as anything you build.

Each is a factory returning a ``Pipeline``; sliceable, extendable, cacheable.
Opinionated numbers live here (screen 10, describe top 3), never in the steps.

- ``quick_probe``   search → screen            — cheap profile iteration
- ``quick_discovery`` quick_probe + inspect + score   (the old find_datasets)
- ``full_report``   quick_discovery + describe
- ``harvest``       full_report + download + export
"""

from __future__ import annotations

from typing import Any

from metabo_search.core.steps import (
    Pipeline,
    describe, download, export, filter, inspect, score, screen, search,
)


def quick_probe(query: str, profile=None, *, databases=None,
                page_size: int = 100, max_results: int = 200,
                min_survivors: int = 10) -> Pipeline:
    """Stage 1: narrow the profile cheaply. Search replays warm across edits."""
    return pipeline_(
        search(query, profile=profile, databases=databases,
               page_size=page_size, max_results=max_results),
        filter(screen(profile=profile, min_survivors=min_survivors)),
    )


def quick_discovery(query: str, profile=None, *, databases=None,
                    page_size: int = 100, max_results: int = 200,
                    min_survivors: int = 10, workers: int = 10,
                    cache_root=None) -> Pipeline:
    """Stage 2 (the old find_datasets): probe + deep inspect + scored ranking."""
    p = quick_probe(query, profile, databases=databases,
                    page_size=page_size,
                    max_results=max_results, min_survivors=min_survivors)
    p = p.extend(inspect(workers=workers), score(profile=profile))
    return p.cache(cache_root) if cache_root else p


def full_report(query: str, profile=None, *, databases=None,
                top: int = 3, revision: int = 0, store: str | None = None,
                page_size: int = 100, max_results: int = 200,
                min_survivors: int = 10, workers: int = 10,
                cache_root=None) -> Pipeline:
    """Discovery + per-sample sentences (ONE LLM call per study, cached)."""
    p = quick_discovery(query, profile, databases=databases,
                        page_size=page_size,
                        max_results=max_results, min_survivors=min_survivors,
                        workers=workers, cache_root=cache_root)
    return p.extend(describe(top=top, revision=revision, store=store))


def harvest(query: str, profile=None, *, databases=None,
            download_kwargs: dict[str, Any] | None = None,
            export_kwargs: dict[str, Any] | None = None,
            top: int = 3, page_size: int = 100, max_results: int = 200,
            min_survivors: int = 10, workers: int = 10,
            cache_root=None) -> Pipeline:
    """Full report + selective download + manifest export.

    ``download_kwargs`` must constrain what is downloaded (categories /
    file_types / sample_names / max_files / max_size_gb) — harvest never
    silently downloads everything.
    """
    dl = download_kwargs or {}
    if not any(k in dl for k in ("categories", "file_types", "sample_names",
                                 "max_files", "max_size_gb")):
        raise ValueError(
            "harvest() needs a download constraint — e.g. "
            "download_kwargs={'categories': ['raw'], 'dest_dir': './data'}")
    p = full_report(query, profile, databases=databases,
                    top=top, page_size=page_size,
                    max_results=max_results, min_survivors=min_survivors,
                    workers=workers, cache_root=cache_root)
    p = p.extend(download(**dl))
    p = p.extend(export(**(export_kwargs or {})))
    return p


# local alias so the module can stay importable even if `pipeline` is shadowed
from metabo_search.core.steps import pipeline as pipeline_  # noqa: E402