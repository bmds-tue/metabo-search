"""Disk cache for the Metabolomics Workbench whole-index corpus.

:meth:`~metabo_search.workbench.client.Workbench.search` filters the whole-index
``/ST/summary`` payload locally, so the client fetches it (slow, ~90 s) and
caches it on disk. The REST API also supports server-side study queries, which
the client does not expose yet. The payload changes rarely, so it is cached per
base URL and reused until its TTL expires.

The *raw* JSON payload is stored, not parsed models, so the cache stays
independent of the model layer.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "metabo-search"
"""Where corpora are cached when the client is not given a directory."""

DEFAULT_TTL = timedelta(days=7)
"""How long a cached corpus is considered fresh."""


class CorpusCache:
    """A per-base-URL JSON cache for the whole-index summary payload.

    Args:
        directory: Directory holding the cache file.
        base_url: API base URL. Part of the cache key, so two hosts never share
            an entry.
        ttl: How long a written entry stays fresh.
    """

    def __init__(self, directory: Path, base_url: str, ttl: timedelta) -> None:
        assert ttl > timedelta(0), "ttl must be positive"
        self._directory = directory
        self._base_url = base_url
        self._ttl = ttl

    @property
    def path(self) -> Path:
        """The cache file for this base URL."""
        digest = hashlib.sha1(self._base_url.encode("utf-8")).hexdigest()[:12]
        return self._directory / f"wb-corpus-{digest}.json"

    def read(self) -> tuple[object, datetime] | None:
        """Return the cached ``(payload, fetched_at)``, or ``None``.

        ``None`` means there is no usable entry: the file is missing,
        unreadable, malformed, or lacks a payload or timestamp.
        """
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        mapping = cast("dict[str, object]", record)
        payload = mapping.get("payload")
        fetched_at = _parse_timestamp(mapping.get("fetched_at"))
        if payload is None or fetched_at is None:
            return None
        return payload, fetched_at

    def is_fresh(self, fetched_at: datetime) -> bool:
        """Whether ``fetched_at`` is within the TTL of now."""
        return datetime.now(UTC) - fetched_at < self._ttl

    def write(self, payload: object) -> None:
        """Atomically write ``payload``, stamped with the current time.

        The payload is written to a temporary file in the same directory and
        then renamed over the target, so a crash never leaves a partial cache.
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        record = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "base_url": self._base_url,
            "payload": payload,
        }
        handle, tmp = tempfile.mkstemp(dir=self._directory, suffix=".json.tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(record, stream)
            os.replace(tmp, self.path)
        except BaseException:
            _unlink(tmp)
            raise


def _parse_timestamp(value: object) -> datetime | None:
    """Parse an ISO8601 timestamp, treating a naive one as UTC."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _unlink(path: str) -> None:
    """Remove a file, ignoring errors (best effort during cleanup)."""
    with contextlib.suppress(OSError):
        os.unlink(path)
