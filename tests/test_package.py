"""Package smoke tests, including the optional ``ingest`` extra."""

from __future__ import annotations

import subprocess
import sys

import metabo_search


def _python(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter and return the completed process."""
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)


def test_package_imports() -> None:
    """The package imports and exposes a public surface list."""
    assert isinstance(metabo_search.__all__, list)


def test_version_is_exposed() -> None:
    """The installed distribution version is exposed as ``__version__``."""
    assert isinstance(metabo_search.__version__, str)
    assert metabo_search.__version__


def test_clients_do_not_import_the_ingest_stack() -> None:
    """Importing the package and clients must not pull in duckdb/pyarrow."""
    result = _python(
        "import sys\n"
        "import metabo_search\n"
        "metabo_search.MetaboLights\n"
        "metabo_search.Workbench\n"
        "assert 'metabo_search.workspace' not in sys.modules, 'workspace imported eagerly'\n"
        "assert 'duckdb' not in sys.modules, 'duckdb imported eagerly'\n"
        "assert 'pyarrow' not in sys.modules, 'pyarrow imported eagerly'\n"
    )
    assert result.returncode == 0, result.stderr


def test_workspace_is_imported_lazily() -> None:
    """``Workspace`` resolves on first attribute access."""
    result = _python(
        "import sys\n"
        "from metabo_search import Workspace\n"
        "assert 'metabo_search.workspace' in sys.modules\n"
        "assert Workspace.__name__ == 'Workspace'\n"
    )
    assert result.returncode == 0, result.stderr


def test_missing_ingest_extra_reports_the_extra() -> None:
    """Without duckdb/pyarrow, ``Workspace`` raises a helpful message."""
    result = _python(
        "import sys\n"
        "class Blocker:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name in {'duckdb', 'pyarrow'}:\n"
        "            raise ModuleNotFoundError(f'No module named {name!r}', name=name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "import metabo_search\n"
        "try:\n"
        "    metabo_search.Workspace\n"
        "except ModuleNotFoundError as exc:\n"
        "    assert 'metabo-search[ingest]' in str(exc), str(exc)\n"
        "else:\n"
        "    raise SystemExit('expected ModuleNotFoundError')\n"
    )
    assert result.returncode == 0, result.stderr
