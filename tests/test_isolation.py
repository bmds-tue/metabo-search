"""The two source packages must stay independent."""

from __future__ import annotations

import ast
from pathlib import Path

import metabo_search

SRC = Path(__file__).parents[1] / "src" / "metabo_search"


def _module_imports(path: Path) -> set[str]:
    """Every module name imported by one Python file."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _imports(package: str) -> set[str]:
    """Every module name imported anywhere in ``package``."""
    modules: set[str] = set()
    for path in (SRC / package).rglob("*.py"):
        modules |= _module_imports(path)
    return modules


def test_metabolights_does_not_import_workbench() -> None:
    assert not any(m.startswith("metabo_search.workbench") for m in _imports("metabolights"))


def test_workbench_does_not_import_metabolights() -> None:
    assert not any(m.startswith("metabo_search.metabolights") for m in _imports("workbench"))


def test_shared_modules_do_not_import_sources() -> None:
    for name in ("workspace.py", "ingest.py"):
        imports = _module_imports(SRC / name)
        assert not any(m.startswith("metabo_search.metabolights") for m in imports), name
        assert not any(m.startswith("metabo_search.workbench") for m in imports), name


def test_public_surface_exposes_clients_and_workspace() -> None:
    assert set(metabo_search.__all__) == {"MetaboLights", "Workbench", "Workspace"}
