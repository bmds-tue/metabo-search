"""MetaboLights repository — adapter over the existing top-level modules.

Delegates to ``metabo_search.searcher`` / ``inspector`` / ``sample_gen`` /
``downloader`` (the MetaboLights-only implementation).  This adapter exists
so the pipeline can treat MetaboLights like any other ``StudyRepository``;
the heavy lifting is unchanged.
"""

from __future__ import annotations

from metabo_search.repositories.base import StudyRepository


class MetabolightsRepository(StudyRepository):
    """The EMBL-EBI MetaboLights v2 index (MTBLS… accession space)."""

    name = "metabolights"

    def search(self, query, *, profile=None, page_size=100, max_results=200,
               filters=None, sort_field=None, sort_direction="desc",
               ms_filters=None, organism=None, technique=None,
               sample_type=None, min_samples=None, min_raw_files=None,
               cache_root=None):
        from metabo_search.searcher import profile_to_search_args, search_studies

        args = profile_to_search_args(profile) if profile else {}
        manual = {
            "filters": filters, "ms_filters": ms_filters,
            "organism": organism, "technique": technique,
            "sample_type": sample_type, "min_samples": min_samples,
            "min_raw_files": min_raw_files,
        }
        args = {**args, **{k: v for k, v in manual.items() if v is not None}}
        if filters or args.get("filters"):
            args["filters"] = list(args.get("filters") or []) + list(filters or [])
        cands = search_studies(
            query=query, page_size=page_size, max_results=max_results,
            sort_field=sort_field, sort_direction=sort_direction, **args)
        for c in cands:
            c.repository = self.name
        return cands, {"filters": args}

    def deep_metadata(self, candidates, *, workers=10, tmp_dir=None,
                      parse_workers=None, cache_root=None):
        from metabo_search.inspector import inspect_studies
        deep = inspect_studies(candidates, max_workers=workers,
                               tmp_dir=tmp_dir, parse_workers=parse_workers)
        for c in deep:
            c.repository = self.name
        return deep

    def sample_rows(self, candidate):
        """Per-sample rows from the ISA sample sheet (sample_metadata +
        file links).  Used by describe/manifest."""
        rows = []
        for m in candidate.sample_metadata or []:
            file_map = (candidate.sample_file_map or {}).get(m.get("sample_name", ""), {})
            rows.append({**m, "data_files": ",".join(
                file_map.get("raw", []) + file_map.get("derived", []))})
        return rows

    def download(self, candidates, dest_dir, *, files_cache_dir=None):
        from metabo_search.downloader import DownloadConfig, download_data_files
        for c in candidates:
            body = DownloadConfig(dest_dir=dest_dir,
                                  files_cache_dir=files_cache_dir)
            download_data_files(c, body)


from metabo_search.repositories.base import DISPATCH

DISPATCH[MetabolightsRepository.name] = MetabolightsRepository()