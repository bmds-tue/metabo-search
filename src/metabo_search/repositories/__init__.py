"""Repository implementations — one subpackage per data source.

The pipeline stays repository-agnostic: it talks to a ``StudyRepository``
seam (``repositories.base``), and each repository adapts a real backend
(MetaboLights, Metabolomics Workbench) into the shared ``StudyCandidate``
model.  Repository-specific code lives only inside its own subpackage.
"""