"""Shared pytest fixtures.

``METABO_WORKBENCH_FIXTURES`` is set globally so the workbench repository
runs fully offline against the captured endpoint fixtures — no network and
no corpus cache writes in tests.
"""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "workbench"


@pytest.fixture(autouse=True)
def _workbench_fixtures_env(monkeypatch):
    monkeypatch.setenv("METABO_WORKBENCH_FIXTURES", str(FIXTURES_DIR))