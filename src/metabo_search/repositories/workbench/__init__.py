"""Metabolomics Workbench (NMDR) repository.

Hit the workbench REST API (``https://www.metabolomicsworkbench.org/rest``,
spec v1.2 2025-07) and normalize its metadata into shared ``StudyCandidate``
objects.  Two search paths:

- **corpus backstop** — the whole index (summary corpus + disease/source/
  species maps) is downloaded once and cached (7 d disk TTL), then filtered
  locally (CPU-only).  This is the deterministic, always-available path.
- **metstat fast path** — server-side slot search, used only when the
  vocabulary matcher is *confident* about species/source/disease.

Nothing here leaks into ``core``; the pipeline sees only ``StudyCandidate``s
tagged ``repository=\"metabolomics_workbench\"``.
"""

# register with the dispatch so `databases=("metabolomics_workbench",)` works
from metabo_search.repositories.base import DISPATCH
from metabo_search.repositories.workbench.repo import WorkbenchRepository

DISPATCH[WorkbenchRepository.name] = WorkbenchRepository()
