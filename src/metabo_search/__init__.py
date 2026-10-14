"""metabo-search — search metabolomics repositories and analyse the results.

Two independent, strictly typed clients (MetaboLights and Metabolomics
Workbench) plus a local DuckDB working set built from search results. The
repositories are never mirrored.

The two clients have no heavy dependencies. :class:`Workspace` (and
``client.ingest``) needs the optional ``ingest`` extra (``duckdb`` +
``pyarrow``) and is therefore imported lazily on first use.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from metabo_search.metabolights import MetaboLights
from metabo_search.workbench import Workbench

if TYPE_CHECKING:
    from metabo_search.workspace import Workspace

__all__ = ["MetaboLights", "Workbench", "Workspace"]

try:
    __version__ = version("metabo-search")
except PackageNotFoundError:  # pragma: no cover - uninstalled source tree
    __version__ = "0.0.0"


def __getattr__(name: str) -> object:
    """Import :class:`~metabo_search.workspace.Workspace` on first use.

    The DuckDB working set is an optional extra, so importing the package and
    the two clients must not require ``duckdb``/``pyarrow``.
    """
    if name == "Workspace":
        from metabo_search.workspace import Workspace

        return Workspace
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
