"""CacheStore — the pipeline's cache root: steps+configs (plan) and results.

Layout::

    <root>/
    ├── plan.json            last run: [{kind, name, config, key, created_at, ttl}]
    ├── results/<key>.json   per-step result payloads
    ├── isa/<MTBLS>/…        persistent ISA dirs (inspect's tmp_dir when unset)
    └── sentences.json       describe's LLM store (revision-keyed, eternal)

Restart semantics (design): the pipeline compares each step's (kind, config)
against the stored plan; unchanged ⇒ the step is skipped and its result
replayed from results/<key>.json. A changed config or changed input digest
produces a different key ⇒ miss ⇒ execute + store + rewrite the plan.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from metabo_search.core.results import (
    Result,
    SearchResult, FilterResult, InspectResult, ScoreResult,
    DescribeResult, DownloadResult, ExportResult,
)

RESULT_CLASSES: dict[str, type[Result]] = {
    "search": SearchResult,
    "filter": FilterResult,
    "inspect": InspectResult,
    "score": ScoreResult,
    "describe": DescribeResult,
    "download": DownloadResult,
    "export": ExportResult,
}

# (enabled-by-default, ttl) per step kind.  The DB is stable: generous TTLs.
DEFAULT_TTL: dict[str, str | None] = {
    "search": "7d",        # the search index moves
    "filter": None,        # cheap; cache off by default
    "inspect": "30d",      # studies publish once
    "score": None,         # cheap; cache off by default
    "describe": None,      # eternal — revision-keyed wording cache
    "download": None,
    "export": None,
}

_TTL_RE = re.compile(r"^(\d+)(s|m|h|d)$")


def parse_ttl(ttl: str | None) -> timedelta | None:
    """'7d' → 7 days; None/'' → never expires."""
    if not ttl:
        return None
    m = _TTL_RE.match(ttl.strip())
    if not m:
        raise ValueError(f"bad ttl {ttl!r} — use like '12h', '7d', '30d'")
    n = int(m.group(1))
    unit = m.group(2)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return timedelta(seconds=n * mult)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CacheStore:
    """File-backed per-step result cache rooted at a single directory."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.results_dir = self.root / "results"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.isa_dir = self.root / "isa"
        self.plan_file = self.root / "plan.json"
        self._plan: list[dict[str, Any]] = []
        if self.plan_file.exists():
            try:
                self._plan = json.loads(self.plan_file.read_text())
            except (json.JSONDecodeError, OSError):
                self._plan = []

    # ── keys ──────────────────────────────────────────────────────────

    def config_json(self, config: Any) -> str:
        """Canonical JSON of a step config (dataclass / plain data)."""
        if dataclasses.is_dataclass(config):
            config = dataclasses.asdict(config)
        return json.dumps(config, sort_keys=True, separators=(",", ":"),
                          default=str)

    def key(self, kind: str, config: Any, input_digest: str) -> str:
        raw = f"{kind}|{self.config_json(config)}|{input_digest}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    # ── plan ──────────────────────────────────────────────────────────

    @property
    def plan(self) -> list[dict[str, Any]]:
        return list(self._plan)

    def plan_record(self, kind: str, name: str | None, config: Any,
                    key: str, ttl: str | None) -> dict[str, Any]:
        return {
            "kind": kind,
            "name": name,
            "config": json.loads(self.config_json(config)),
            "key": key,
            "created_at": _now(),
            "ttl": ttl,
        }

    def has_plan(self) -> bool:
        return bool(self._plan)

    def save_plan(self, entries: list[dict[str, Any]]) -> None:
        self._plan = entries
        self.plan_file.write_text(
            json.dumps(entries, indent=1, sort_keys=True))

    # ── results ──────────────────────────────────────────────────────

    def lookup(self, kind: str, key: str, ttl: str | None) -> Result | None:
        """Return a fresh cached result, or None on miss/stale."""
        path = self.results_dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        created = data.get("_created_at")
        if ttl and created:
            created_at = datetime.fromisoformat(created)
            if datetime.now(timezone.utc) - created_at > parse_ttl(ttl):
                return None
        cls = RESULT_CLASSES.get(kind)
        if cls is None:
            return None
        try:
            return cls.from_json(json.dumps(data.get("result", data)))
        except Exception:
            return None

    def store(self, kind: str, key: str, result: Result, ttl: str | None) -> None:
        path = self.results_dir / f"{key}.json"
        payload = json.loads(result.to_json())
        payload["_created_at"] = _now()
        payload["_ttl"] = ttl
        path.write_text(json.dumps(payload, sort_keys=True))

    def isa_path(self, study_id: str) -> Path:
        return self.isa_dir / study_id

    # convenience: where describe's sentence store lives by default
    @property
    def sentences_file(self) -> Path:
        return self.root / "sentences.json"