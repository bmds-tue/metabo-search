"""Fixtures for the live (network) suite.

These tests hit the real MetaboLights and Metabolomics Workbench APIs. They are
marked ``live`` and excluded from the default run. See ``docs/ci.md``.
"""

from __future__ import annotations

import pytest

from metabo_search import MetaboLights, Workbench
from metabo_search.transport import RateLimit, RetryPolicy

# Patient retries and a modest rate limit: the live job must be polite and must
# not fail on the transient 5xx the repositories occasionally return.
_RETRY = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=30.0)
_RATE = RateLimit(requests_per_second=5.0, burst=5)


@pytest.fixture(scope="session")
def ml() -> MetaboLights:
    """A session MetaboLights client with patient retries."""
    with MetaboLights(retry=_RETRY, rate_limit=_RATE) as client:
        yield client


@pytest.fixture(scope="session")
def wb() -> Workbench:
    """A session Workbench client. ``cache_dir=None`` keeps the fast suite
    independent of (and free of writes to) the whole-index corpus cache."""
    with Workbench(retry=_RETRY, rate_limit=_RATE, cache_dir=None) as client:
        yield client
