"""Step result types — the typed outputs of every pipeline step.

Every result is plain data with four capabilities:

- ``to_json()`` / ``from_json()`` — round-trip through JSON (the cache medium).
  ``StudyCandidate._raw_api_result`` (unbounded raw API junk) is excluded.
- ``digest()`` — stable identity (over full serialized state) for cache keys.
- ``fmt(detail=False)`` — compact human/LLM-readable print.
- Nothing else.

Serialization is generic over dataclasses: ``to_dict`` uses
``dataclasses.asdict``, ``rebuild`` reconstructs via ``typing.get_type_hints``
(handles Optional / list / dict / tuple / Union of dataclasses).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import typing
from typing import Any

from metabo_search.models import (
    ComparisonReport,
    ScoredCandidate,
    StudyCandidate,
)
from metabo_search.sample_gen import SampleDescription

# ──────────────────────────────────────────────────────────────
# Generic dataclass serialization
# ──────────────────────────────────────────────────────────────


def _plain_factory(items):
    """dict_factory for asdict: drop private fields (e.g. ``_raw_api_result``)
    at every nesting level."""
    return {k: v for k, v in items if not k.startswith("_")}


def to_dict(obj: Any) -> Any:
    """Dataclass → JSON-plain dict. Private fields (``_raw_api_result``) dropped."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj, dict_factory=_plain_factory)
    return obj


def _from(ty: Any, data: Any) -> Any:
    if data is None:
        return None
    if ty is None or ty is Any or ty is object:
        return data
    origin = typing.get_origin(ty)
    if origin is None:
        if isinstance(ty, type) and dataclasses.is_dataclass(ty):
            hints = typing.get_type_hints(ty)
            kwargs = {}
            for f in dataclasses.fields(ty):
                if f.name.startswith("_"):
                    continue
                if f.name in data:
                    kwargs[f.name] = _from(hints.get(f.name, Any), data[f.name])
            return ty(**kwargs)
        if ty is bool:
            return data if isinstance(data, bool) else data
        if ty is int:
            return int(data) if isinstance(data, (int, float)) and not isinstance(data, bool) else data
        if ty is float:
            return float(data) if isinstance(data, (int, float)) and not isinstance(data, bool) else data
        if ty is str:
            return data if isinstance(data, str) else data
        return ty(data)
    args = typing.get_args(ty)
    if origin in (list, list):
        (item_ty,) = args
        return [_from(item_ty, x) for x in data]
    if origin in (dict, dict):
        k_ty, v_ty = args
        return {k: _from(v_ty, v) for k, v in data.items()}
    if origin in (tuple, tuple):
        return tuple(_from(a, x) for a, x in zip(args, data))
    if origin is typing.Union:
        for a in args:
            if a is type(None):
                continue
            if isinstance(a, type):
                if dataclasses.is_dataclass(a):
                    try:
                        return _from(a, data)
                    except Exception:
                        continue
                if isinstance(data, a):
                    return data
                continue
            try:
                return _from(a, data)
            except Exception:
                continue
        return data
    return data


def rebuild(ty: type, data: dict) -> Any:
    return _from(ty, data)


def candidates_to_json(cands: list[StudyCandidate]) -> str:
    """Canonical JSON for a list of ``StudyCandidate`` (cache medium).

    Same rules as ``Result.to_json`` (private fields dropped, keys sorted,
    compact) so a candidate list stored on disk is byte-identical across
    fresh/warm runs — the reproducibility guarantee of the per-db cache.
    """
    payload = [dataclasses.asdict(c, dict_factory=_plain_factory)
               for c in cands]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def candidates_from_json(s: str) -> list[StudyCandidate]:
    """Inverse of :func:`candidates_to_json`.

    Rebuilds dataclasses (nested OntologyTerm/AssayInfo/… included) via the
    generic ``rebuild`` path; returns a fresh list by value.
    """
    return [rebuild(StudyCandidate, d) for d in json.loads(s)]


# ──────────────────────────────────────────────────────────────
# Result base
# ──────────────────────────────────────────────────────────────


class Result:
    """Base class for every step output. Subclasses are dataclasses."""

    def to_dict(self) -> dict:
        return {"_kind": type(self).__name__, **(
            dataclasses.asdict(self, dict_factory=_plain_factory))}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def rebuild(cls, data: dict) -> Result:
        return _from(cls, data)

    @classmethod
    def from_json(cls, s: str) -> Result:
        return cls.rebuild(json.loads(s))

    def digest(self) -> str:
        """Stable identity: sha256 over canonical JSON of full state."""
        payload = self.to_dict()
        payload.pop("_kind", None)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def fmt(self, detail: bool = False) -> str:
        raise NotImplementedError


# ── candidate-listing helper (shared by search/filter/inspect fmt) ──


def _fmt_cands(cands: list[StudyCandidate], detail: bool = False,
               meta: str = "") -> str:
    lines = [f"{len(cands)} candidate(s)" + (f" {meta}" if meta else "")]
    for c in cands:
        depth = c.inspection_depth
        counts = f" samples={c.sample_count or '?'}"
        if depth == "deep" and c.metabolite_count:
            counts += f" maf={c.metabolite_count}"
        lines.append(f"  {c.study_id} · {(c.title or '')[:70]} · {depth}{counts}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Step results
# ──────────────────────────────────────────────────────────────


@dataclasses.dataclass
class SearchResult(Result):
    candidates: list[StudyCandidate]
    query: str = ""
    args_used: dict[str, Any] = dataclasses.field(default_factory=dict)

    def fmt(self, detail: bool = False) -> str:
        lines = [_fmt_cands(self.candidates, detail, f"query '{self.query}'")]
        # per-repository notices (workbench vocabulary ambiguity) are the
        # agent's actionable instructions — always visible.
        for db, meta in self.args_used.items():
            if isinstance(meta, dict) and meta.get("notice"):
                lines.append(f"[{db}] notice: {meta['notice']}")
        return "\n".join(lines)


@dataclasses.dataclass
class FilterResult(Result):
    survivors: list[StudyCandidate]
    dropped: list[tuple[StudyCandidate, str]] = dataclasses.field(default_factory=list)
    order: dict[str, float] = dataclasses.field(default_factory=dict)
    stage: str = ""   # "shallow" | "deep" — which carrier this filter ran on

    def fmt(self, detail: bool = False) -> str:
        head = _fmt_cands(self.survivors, detail,
                          f"({len(self.dropped)} dropped)")
        if not detail:
            return head
        for c, reason in self.dropped[:20]:
            head += f"\n  ✗ {c.study_id} — {reason}"
        if len(self.dropped) > 20:
            head += f"\n  … {len(self.dropped) - 20} more dropped"
        return head


@dataclasses.dataclass
class InspectResult(Result):
    candidates: list[StudyCandidate]
    isa_dirs: dict[str, str] = dataclasses.field(default_factory=dict)

    def fmt(self, detail: bool = False) -> str:
        return _fmt_cands(self.candidates, detail)


@dataclasses.dataclass
class ScoreResult(Result):
    ranked: list[ScoredCandidate]
    table: ComparisonReport

    def fmt(self, detail: bool = False) -> str:
        lines = [f"{len(self.ranked)} ranked"]
        for sc in self.ranked[: (20 if detail else 10)]:
            c = sc.candidate
            mark = "✓" if sc.score.hard_passed else "·"
            lines.append(
                f"  {mark} {c.study_id}  {sc.score.overall:.2f}  "
                f"{(c.title or '')[:60]}  samples={c.sample_count or '?'}"
            )
        if len(self.ranked) > 10 and not detail:
            lines.append(f"  … {len(self.ranked) - 10} more")
        return "\n".join(lines)


@dataclasses.dataclass
class DescribeResult(Result):
    by_study: dict[str, list[SampleDescription]] = dataclasses.field(default_factory=dict)
    revision: int = 0
    reused: dict[str, bool] = dataclasses.field(default_factory=dict)

    def fmt(self, detail: bool = False) -> str:
        lines = [f"{len(self.by_study)} study(ies) described (revision r{self.revision})"]
        for sid, descs in self.by_study.items():
            hit = "cached" if self.reused.get(sid) else "generated"
            lines.append(f"  {sid} [{hit}] {len(descs)} sentences")
            if detail and descs:
                lines.append(f"    e.g. {descs[0].sentence[:150]}")
        return "\n".join(lines)


@dataclasses.dataclass
class DownloadResult(Result):
    dest_dir: str = ""
    downloaded: list[str] = dataclasses.field(default_factory=list)
    total_bytes: int = 0
    failed: list[str] = dataclasses.field(default_factory=list)

    def fmt(self, detail: bool = False) -> str:
        lines = [
            f"{len(self.downloaded)} file(s) → {self.dest_dir or '—'} "
            f"({self.total_bytes / 1e6:.1f} MB)"
        ]
        if detail:
            for p in self.downloaded[:20]:
                lines.append(f"  {p}")
            for f in self.failed[:10]:
                lines.append(f"  ✗ {f}")
        return "\n".join(lines)


@dataclasses.dataclass
class ExportResult(Result):
    path: str = ""
    rows: int = 0
    columns: list[str] = dataclasses.field(default_factory=list)

    def fmt(self, detail: bool = False) -> str:
        return f"{self.rows} rows × {len(self.columns)} cols → {self.path or '—'}"