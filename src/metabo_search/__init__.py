"""metabo-search — search metabolomics repositories and analyse the results.

Two independent, strictly typed clients (MetaboLights and Metabolomics
Workbench) plus a local DuckDB working set built from search results. The
repositories are never mirrored.

The DuckDB layer is added in a later step; see ``docs/api-design.md`` for the
target API.
"""

from metabo_search.metabolights import MetaboLights
from metabo_search.workbench import Workbench

__all__ = ["MetaboLights", "Workbench"]
