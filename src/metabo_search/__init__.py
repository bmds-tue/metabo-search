"""metabo-search — search metabolomics repositories and analyse the results.

Two independent, strictly typed clients (MetaboLights and Metabolomics
Workbench) plus a local DuckDB working set built from search results. The
repositories are never mirrored.
"""

from metabo_search.metabolights import MetaboLights
from metabo_search.workbench import Workbench
from metabo_search.workspace import Workspace

__all__ = ["MetaboLights", "Workbench", "Workspace"]
