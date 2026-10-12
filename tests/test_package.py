"""Package smoke tests."""

import metabo_search


def test_package_imports() -> None:
    """The package imports and exposes a public surface list."""
    assert isinstance(metabo_search.__all__, list)
