"""metabo-search — search metabolomics repositories and analyse the results.

Two independent, strictly typed clients (MetaboLights and Metabolomics
Workbench) plus a local DuckDB working set built from search results. The
repositories are never mirrored.

The Workbench client and the DuckDB layer are added in later steps; see
``docs/api-design.md`` for the target API.
"""

from metabo_search.metabolights import MetaboLights

__all__ = ["MetaboLights"]
