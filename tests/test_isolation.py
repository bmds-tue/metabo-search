"""The two source packages must stay independent."""

from __future__ import annotations

import ast
from pathlib import Path

import metabo_search

SRC = Path(__file__).parents[1] / "src" / "metabo_search"


def _imports(package: str) -> set[str]:
    """Every module name imported anywhere in ``package``."""
    modules: set[str] = set()
    for path in (SRC / package).rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
    return modules


def test_metabolights_does_not_import_workbench() -> None:
    assert not any(m.startswith("metabo_search.workbench") for m in _imports("metabolights"))


def test_workbench_does_not_import_metabolights() -> None:
    assert not any(m.startswith("metabo_search.metabolights") for m in _imports("workbench"))


def test_public_surface_exposes_both_clients() -> None:
    assert set(metabo_search.__all__) == {"MetaboLights", "Workbench"}
