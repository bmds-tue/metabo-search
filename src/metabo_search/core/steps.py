"""Steps + Pipeline — the typed flow engine.

A step is ``Config → Result``, named by what it does to the data.  A pipeline
is an ordered list of steps; step N's output type is step N+1's input type.
Annex steps (describe / download / export) consume the latest result of their
required type produced so far and never mutate the main chain.

Design: docs/design-pipeline.md
"""

from __future__ import annotations

import copy
import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from metabo_search.core.cache import DEFAULT_TTL, RESULT_CLASSES, CacheStore
from metabo_search.core.results import (
    Result,
    SearchResult, FilterResult, InspectResult, ScoreResult,
    DescribeResult, DownloadResult, ExportResult,
)
from metabo_search.models import (
    FitnessScore,
    RequirementProfile,
    ScoredCandidate,
    StudyCandidate,
)

# ──────────────────────────────────────────────────────────────
# Cross-cutting options
# ──────────────────────────────────────────────────────────────


@dataclass
class CacheOpts:
    enabled: bool | None = None   # None = inherit the per-kind pipeline default
    ttl: str | None = None        # None = inherit; "7d" | "12h" | "30d" | "forever"


@dataclass
class PrintOpts:
    top: int = 10
    detail: bool = False


# ──────────────────────────────────────────────────────────────
# Step configs (plain data, identity-by-default)
# ──────────────────────────────────────────────────────────────


@dataclass
class SearchConfig:
    query: str                                      # ⛔ required
    profile: RequirementProfile | None = None
    page_size: int = 100
    max_results: int = 200
    filters: list[dict] | None = None
    ms_filters: dict | None = None
    sort_field: str | None = None
    sort_direction: str = "desc"
    organism: str | list[str] | None = None
    technique: str | list[str] | None = None
    sample_type: str | list[str] | None = None
    min_samples: int | None = None
    min_raw_files: int | None = None


@dataclass
class FilterConfig:
    predicates: list[Any] = field(default_factory=list)   # ⛔ ≥1 (validated)


@dataclass
class InspectConfig:
    workers: int = 10
    tmp_dir: str | None = None
    parse_workers: int | None = None   # None = auto (processes for ≥8 studies)


@dataclass
class ScoreConfig:
    profile: RequirementProfile | None = None
    columns: list[str] | None = None


@dataclass
class DescribeConfig:
    top: int = 3
    revision: int = 0
    store: str | None = None


@dataclass
class DownloadConfig:
    dest_dir: str = "."
    categories: list[str] | None = None      # "raw" | "derived" | "other"
    file_types: list[str] | None = None
    sample_names: list[str] | None = None
    max_files: int | None = None
    max_size_gb: float | None = None
    parallel: int = 4


@dataclass
class ExportConfig:
    path: str = "manifest.csv"
    include_sentences: bool = True
    with_traceability: bool = True


# ──────────────────────────────────────────────────────────────
# Predicates — the granularity of filter()
# ──────────────────────────────────────────────────────────────

_REGISTRY: dict[str, tuple[type, Callable]] = {}


def register_predicate(name: str, applies_to: type, fn: Callable) -> None:
    """Register a custom filter predicate by name (configs stay plain data)."""
    _REGISTRY[name] = (applies_to, fn)


def registered_predicates() -> list[str]:
    return sorted(_REGISTRY)


@dataclass
class Screen:
    profile: RequirementProfile | None = None
    min_survivors: int | None = None   # None = never truncate (identity)
    name: str = "screen"


@dataclass
class Maf:
    require: bool = True
    min_metabolites: int | None = None
    name: str = "maf"


@dataclass
class Custom:
    name: str
    params: dict = field(default_factory=dict)


def predicate_applies_to(p: Any) -> type:
    if isinstance(p, Screen):
        return SearchResult
    if isinstance(p, Maf):
        return InspectResult
    if isinstance(p, Custom):
        ent = _REGISTRY.get(p.name)
        if ent is None:
            raise ValueError(
                f"unknown predicate {p.name!r} — call register_predicate(...) first")
        return ent[0]
    raise TypeError(f"not a predicate: {p!r}")


def _predicate_name(p: Any) -> str:
    return p.name if hasattr(p, "name") else type(p).__name__


# ──────────────────────────────────────────────────────────────
# Step
# ──────────────────────────────────────────────────────────────


@dataclass
class Step:
    kind: str
    config: Any
    name: str | None = None
    cache: CacheOpts = field(default_factory=CacheOpts)
    print: PrintOpts = field(default_factory=PrintOpts)

    @property
    def label(self) -> str:
        return self.name or self.kind

    def __str__(self) -> str:
        return _step_label(self)


def _step_label(step: Step) -> str:
    cfg = step.config
    brief = ""
    if step.kind == "search":
        brief = f" {cfg.query!r}"
    elif step.kind == "filter":
        brief = " " + ", ".join(_predicate_name(p) for p in cfg.predicates)
    return f"{step.kind}({brief.strip()})" if brief else step.kind


# ──────────────────────────────────────────────────────────────
# Step factories — the public API
# ──────────────────────────────────────────────────────────────


def search(query: str, *, profile=None, page_size: int = 100,
           max_results: int = 200, filters: list[dict] | None = None,
           ms_filters: dict | None = None, sort_field: str | None = None,
           sort_direction: str = "desc",
           organism=None, technique=None, sample_type=None,
           min_samples: int | None = None, min_raw_files: int | None = None,
           name: str | None = None, cache: CacheOpts | None = None,
           print_opts: PrintOpts | None = None) -> Step:
    if not query:
        raise ValueError("search() needs a query")
    return Step("search", SearchConfig(
        query=query, profile=profile, page_size=page_size,
        max_results=max_results, filters=filters, ms_filters=ms_filters,
        sort_field=sort_field, sort_direction=sort_direction,
        organism=organism, technique=technique, sample_type=sample_type,
        min_samples=min_samples, min_raw_files=min_raw_files),
        name=name, cache=cache or CacheOpts(), print=print_opts or PrintOpts())


def filter(*predicates: Any, name: str | None = None,
           cache: CacheOpts | None = None,
           print_opts: PrintOpts | None = None) -> Step:
    if not predicates:
        raise ValueError("filter() needs at least one predicate "
                         "(screen(...), maf(...), or a registered custom)")
    return Step("filter", FilterConfig(list(predicates)),
                name=name, cache=cache or CacheOpts(),
                print=print_opts or PrintOpts())


def screen(profile: RequirementProfile | None = None,
           min_survivors: int | None = None) -> Screen:
    """Shallow screening predicate (applies to SearchResult).

    ``min_survivors=None`` ⇒ never truncate (identity); set a cap to bound
    the next stage (recipes set 10)."""
    return Screen(profile=profile, min_survivors=min_survivors)


def maf(require: bool = True, min_metabolites: int | None = None) -> Maf:
    """MAF (metabolite assignment) predicate (applies to InspectResult)."""
    return Maf(require=require, min_metabolites=min_metabolites)


def custom(name: str, **params: Any) -> Custom:
    """Custom predicate reference (must be registered before validate/run)."""
    return Custom(name=name, params=params)


def inspect(workers: int = 10, tmp_dir: str | None = None,
            parse_workers: int | None = None,
            name: str | None = None, cache: CacheOpts | None = None,
            print_opts: PrintOpts | None = None) -> Step:
    return Step("inspect", InspectConfig(workers=workers, tmp_dir=tmp_dir,
                                          parse_workers=parse_workers),
                name=name, cache=cache or CacheOpts(),
                print=print_opts or PrintOpts())


def score(profile: RequirementProfile | None = None,
          columns: list[str] | None = None, name: str | None = None,
          cache: CacheOpts | None = None,
          print_opts: PrintOpts | None = None) -> Step:
    return Step("score", ScoreConfig(profile=profile, columns=columns),
                name=name, cache=cache or CacheOpts(),
                print=print_opts or PrintOpts())


def describe(top: int = 3, revision: int = 0, store: str | None = None,
             name: str | None = None, cache: CacheOpts | None = None,
             print_opts: PrintOpts | None = None) -> Step:
    return Step("describe", DescribeConfig(top=top, revision=revision,
                                           store=store),
                name=name, cache=cache or CacheOpts(),
                print=print_opts or PrintOpts())


def download(dest_dir: str = ".", categories=None, file_types=None,
             sample_names=None, max_files: int | None = None,
             max_size_gb: float | None = None, parallel: int = 4,
             name: str | None = None, cache: CacheOpts | None = None,
             print_opts: PrintOpts | None = None) -> Step:
    return Step("download", DownloadConfig(
        dest_dir=dest_dir, categories=categories, file_types=file_types,
        sample_names=sample_names, max_files=max_files,
        max_size_gb=max_size_gb, parallel=parallel),
        name=name, cache=cache or CacheOpts(),
        print=print_opts or PrintOpts())


def export(path: str = "manifest.csv", include_sentences: bool = True,
           with_traceability: bool = True, name: str | None = None,
           cache: CacheOpts | None = None,
           print_opts: PrintOpts | None = None) -> Step:
    return Step("export", ExportConfig(
        path=path, include_sentences=include_sentences,
        with_traceability=with_traceability),
        name=name, cache=cache or CacheOpts(),
        print=print_opts or PrintOpts())


# ──────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────


class PipelineResult:
    """Ordered, typed mapping of step keys → results (r["score"], r.score)."""

    def __init__(self, items: list[tuple[str, Result]]):
        self._items = list(items)
        self._map = dict(items)

    @property
    def order(self) -> list[str]:
        return [k for k, _ in self._items]

    def keys(self) -> list[str]:
        return self.order

    def __getitem__(self, key: str) -> Result:
        return self._map[key]

    def __getattr__(self, key: str) -> Result:
        if key in self._map:
            return self._map[key]
        raise AttributeError(key)

    def get(self, key: str, default: Any = None) -> Result | None:
        return self._map.get(key, default)

    def latest(self, cls: type) -> Result | None:
        for _, r in reversed(self._items):
            if isinstance(r, cls):
                return r
        return None

    def fmt(self, detail: bool = False) -> str:
        return "\n".join(f"{k}:\n{r.fmt(detail)}" for k, r in self._items)

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


class Pipeline:
    """An ordered list of typed steps. Immutable: builders return a new one.

    - ``run(llm=...)`` folds the steps; the cache root replays warm steps
      (each step's cache key covers kind + config + input digest).
    - ``input=`` at construction starts midstream from any typed result.
    - ``extend`` / slicing give "stop anywhere, continue via warm cache".
    """

    def __init__(self, *steps: Step, input: Result | None = None,
                 cache_root: str | Path | None = None):
        if not isinstance(input, (Result, type(None))):
            raise TypeError(f"pipeline input must be a Result, got {input!r}")
        self.steps: list[Step] = list(steps)
        self.input: Result | None = input
        self.cache_root = Path(cache_root) if cache_root else None
        self._cache: CacheStore | None = (
            CacheStore(self.cache_root) if self.cache_root else None)

    # ── immutable builders ──────────────────────────────────────────

    def extend(self, *steps: Step) -> "Pipeline":
        return Pipeline(*self.steps, *steps, input=self.input,
                        cache_root=self.cache_root)

    def cache(self, root: str | Path | None) -> "Pipeline":
        """Set (or clear, with None) the cache root."""
        return Pipeline(*self.steps, input=self.input, cache_root=root)

    # ── plan-time validation ────────────────────────────────────────

    def _errors(self) -> list[str]:
        errs: list[str] = []
        # ``chain`` = the last produced result type; ``stage`` = the original
        # carrier class the chain currently operates on (SearchResult for the
        # shallow stage, InspectResult for the deep stage, ScoreResult after
        # scoring).  filter() keeps the current stage; inspect() moves to deep.
        chain: type | None = type(self.input) if self.input else None
        stage: type | None = None
        if isinstance(self.input, FilterResult):
            stage = InspectResult if getattr(self.input, "stage", "") == "deep" \
                else SearchResult
        elif isinstance(self.input, SearchResult):
            stage = SearchResult
        elif isinstance(self.input, InspectResult):
            stage = InspectResult
        elif isinstance(self.input, ScoreResult):
            stage = ScoreResult
        seen: set[type] = set()
        if chain:
            seen.add(chain)

        for i, step in enumerate(self.steps):
            k, cfg = step.kind, step.config
            where = f"step {i} ({_step_label(step)})"

            if k == "search":
                if chain is not None and chain is not SearchResult:
                    errs.append(f"{where}: search() must start the chain "
                                f"(or follow a SearchResult input), not a "
                                f"{chain.__name__}")
                chain, stage = SearchResult, SearchResult

            elif k == "filter":
                if chain is None:
                    errs.append(f"{where}: filter() needs a previous result "
                                f"(or pipeline(input=...))")
                elif chain not in (SearchResult, InspectResult, FilterResult):
                    errs.append(f"{where}: filter() can only sit on a search-, "
                                f"inspect-, or filter-result stage, not a "
                                f"{chain.__name__}")
                for p in cfg.predicates:
                    need = predicate_applies_to(p)
                    if isinstance(need, type) and stage is not None and \
                            need is not stage:
                        lbl = {SearchResult: "shallow (search)",
                               InspectResult: "deep (inspect)"}.get(
                                   stage, stage.__name__)
                        errs.append(
                            f"{where}: predicate {_predicate_name(p)!r} applies "
                            f"to {need.__name__}, but this filter runs at the "
                            f"{lbl} stage — move it to its stage")
                if not cfg.predicates:
                    errs.append(f"{where}: filter() needs ≥1 predicate")
                chain = FilterResult          # stage unchanged

            elif k == "inspect":
                if chain is None or chain not in (SearchResult, FilterResult):
                    errs.append(f"{where}: inspect() needs a shallow carrier "
                                f"(search result or a pre-inspect filter)")
                elif stage is not SearchResult:
                    errs.append(f"{where}: inspect() needs shallow candidates — "
                                f"the chain is already deep")
                chain, stage = InspectResult, InspectResult

            elif k == "score":
                if stage is not InspectResult:
                    errs.append(f"{where}: score() needs deep candidates — "
                                f"add inspect() before it")
                chain, stage = ScoreResult, ScoreResult

            elif k == "describe":
                if ScoreResult not in seen and chain is not ScoreResult:
                    errs.append(f"{where}: describe() reads the latest "
                                f"score — add score() before it")
            elif k == "download":
                if InspectResult not in seen and chain is not InspectResult:
                    errs.append(f"{where}: download() needs inspected "
                                f"candidates — add inspect() before it")
                n = sum(1 for x in (
                    cfg.categories, cfg.file_types, cfg.sample_names,
                    cfg.max_files, cfg.max_size_gb) if x is not None)
                if n == 0:
                    errs.append(f"{where}: download() with no constraint "
                                f"(categories/file_types/sample_names/"
                                f"max_files/max_size_gb) would fetch "
                                f"everything — refuse")
            elif k == "export":
                if InspectResult not in seen and chain is not InspectResult:
                    errs.append(f"{where}: export() needs inspected "
                                f"candidates — add inspect() before it")

            seen.add(chain) if chain else None
        return errs

    def validate(self) -> None:
        errs = self._errors()
        if errs:
            raise ValueError("pipeline validation failed:\n  - " +
                             "\n  - ".join(errs))

    # ── diff / ladder ───────────────────────────────────────────────

    def _cfg_repr(self, cfg: Any) -> str:
        if cfg is None:
            return "None"
        if isinstance(cfg, dict):
            return json_dumps_sorted(cfg)
        d = dataclasses.asdict(cfg) if dataclasses.is_dataclass(cfg) else cfg
        return json_dumps_sorted(d)

    @staticmethod
    def _val_repr(v: Any) -> str:
        if dataclasses.is_dataclass(v):
            s = json_dumps_sorted(dataclasses.asdict(v))
        elif isinstance(v, list) and v and all(
                dataclasses.is_dataclass(x) for x in v):
            s = ", ".join(Pipeline._pred_repr(x) for x in v)
        elif isinstance(v, list):
            s = repr(v)
        else:
            s = repr(v)
        return (s[:120] + "…") if len(s) > 120 else s

    @staticmethod
    def _pred_repr(p: Any) -> str:
        base = getattr(p, "name", type(p).__name__.lower())
        bits = [base]
        for f in dataclasses.fields(p):
            if f.name == "name":
                continue
            v = getattr(p, f.name)
            if v in (None, False, {}, [], "", 0):
                continue
            bits.append(f"{f.name}={Pipeline._val_repr(v)}")
        return f"{base}({', '.join(b for b in bits[1:])})"

    @staticmethod
    def _field_map(cfg: Any) -> dict:
        if not dataclasses.is_dataclass(cfg):
            return {"value": cfg}
        return {f.name: getattr(cfg, f.name) for f in dataclasses.fields(cfg)}

    @staticmethod
    def _short(v: Any) -> str:
        s = Pipeline._val_repr(v)
        return (s[:64] + "…") if len(s) > 64 else s

    @staticmethod
    def _config_deltas(ca: Any, cb: Any) -> list[str]:
        lines = []
        key = "predicates"
        fa, fb = Pipeline._field_map(ca), Pipeline._field_map(cb)
        pa, pb = fa.get(key), fb.get(key)
        if isinstance(pa, list) and isinstance(pb, list) and pa and pb and \
                all(dataclasses.is_dataclass(x) for x in pa + pb):
            # predicate-level deltas (the important part of filter())
            for i in range(max(len(pa), len(pb))):
                xa = pa[i] if i < len(pa) else None
                xb = pb[i] if i < len(pb) else None
                if xa is None:
                    lines.append(f"    +predicates[{i}]: +{_predicate_name(xb)}")
                    continue
                if xb is None:
                    lines.append(f"    +predicates[{i}]: −{_predicate_name(xa)}")
                    continue
                mfa, mfb = Pipeline._field_map(xa), Pipeline._field_map(xb)
                for fk in sorted(set(mfa) | set(mfb)):
                    if fk == "name":
                        continue
                    if mfa.get(fk) != mfb.get(fk):
                        lines.append(
                            f"    +predicates[{i}].{fk}: "
                            f"{Pipeline._short(mfa.get(fk))} → "
                            f"{Pipeline._short(mfb.get(fk))}")
            return lines or [f"    +predicates: changed"]
        for k in sorted(set(fa) | set(fb)):
            if fa.get(k) != fb.get(k):
                lines.append(f"    +{k}: {Pipeline._short(fa.get(k))}"
                             f" → {Pipeline._short(fb.get(k))}")
        return lines

    def diff(self, other: "Pipeline") -> str:
        a, b = self.steps, other.steps
        out = []
        for i in range(max(len(a), len(b))):
            sa = a[i] if i < len(a) else None
            sb = b[i] if i < len(b) else None
            if sa is None:
                out.append(f"+ {_step_label(sb)}")
            elif sb is None:
                out.append(f"− {_step_label(sa)}")
            elif sa.kind != sb.kind:
                out.append(f"~ {_step_label(sa)}  →  {_step_label(sb)}")
            elif sa.config != sb.config:
                out.append(f"~ {_step_label(sb)}")
                out += Pipeline._config_deltas(sa.config, sb.config)
            if sa is not None and sb is not None:
                if sa.cache != sb.cache:
                    out.append(f"  ~cache: {sa.cache} → {sb.cache}")
                if sa.print != sb.print:
                    out.append(f"  ~print: {sa.print} → {sb.print}")
        if self.cache_root != other.cache_root:
            out.append(f"~ cache root: {self.cache_root} → {other.cache_root}")
        return "\n".join(out) if out else "(unchanged)"

    def __str__(self) -> str:
        return " → ".join(_step_label(s) for s in self.steps)

    # ── run ─────────────────────────────────────────────────────────

    def run(self, input: Result | None = None, llm: Callable | None = None,
            force: bool = False) -> PipelineResult:
        init = self.input if input is None else input
        if init is not None and self.steps:
            first = self.steps[0]
            if first.kind == "search" and not isinstance(init, SearchResult):
                raise TypeError(
                    f"run(input=...) for search() must be a SearchResult, "
                    f"got {type(init).__name__}")
        self.validate()

        # Results cross step boundaries BY VALUE: downstream steps (notably
        # inspect) merge enrichment into their input candidates in place, so
        # each handoff is a deep copy — upstream results stay pristine and
        # their digests stay stable (fresh vs warm-cache).
        chain: Result | None = copy.deepcopy(init) if init is not None else None

        results: list[tuple[str, Result]] = []
        seen_names: dict[str, int] = {}

        def key_for(step: Step) -> str:
            base = step.name or step.kind
            n = seen_names.get(base, 0)
            seen_names[base] = n + 1
            return base if n == 0 else f"{base}#{n + 1}"

        def previous(kind_cls: type) -> Result | None:
            for _, r in reversed(results):
                if isinstance(r, kind_cls):
                    return r
            return chain if isinstance(chain, kind_cls) else None

        used_plan: list[dict[str, Any]] = []

        for step in self.steps:
            enabled = self._cache_enabled(step)
            kind = step.kind
            in_digest = chain.digest() if chain is not None else "init"
            cached: Result | None = None
            key = ""
            if self._cache and enabled:
                key = self._cache.key(kind, step.config, in_digest)
                if not force:
                    cached = self._cache.lookup(kind, key, self._ttl_for(step))
                    if cached is not None:
                        used_plan.append(self._cache.plan_record(
                            kind, step.name, step.config, key,
                            self._ttl_for(step)))

            if cached is not None:
                result = cached
            else:
                result = self._execute(step, chain, results, previous, llm)
                if self._cache and enabled:
                    self._cache.store(kind, key, result, self._ttl_for(step))
                    used_plan.append(self._cache.plan_record(
                        kind, step.name, step.config, key,
                        self._ttl_for(step)))

            snapshot = copy.deepcopy(result)   # pristine copy → results map
            results.append((key_for(step), snapshot))
            if kind in ("search", "filter", "inspect", "score"):
                # a SEPARATE copy feeds the next step (which may mutate its
                # input in place, e.g. inspect merging into candidates)
                chain = copy.deepcopy(result)

        if self._cache:
            self._cache.save_plan(used_plan)
        return PipelineResult(results)

    # ── internals ───────────────────────────────────────────────────

    def _cache_enabled(self, step: Step) -> bool:
        if step.kind not in RESULT_CLASSES:
            return False
        if step.cache.enabled is not None:
            return step.cache.enabled
        return step.kind in ("search", "inspect", "describe")

    def _ttl_for(self, step: Step) -> str | None:
        if step.cache.ttl is not None:
            return step.cache.ttl
        return DEFAULT_TTL.get(step.kind)

    def _execute(self, step, chain, results, previous, llm) -> Result:
        kind, cfg = step.kind, step.config
        cache_root = self.cache_root
        if kind == "search":
            return _do_search(cfg)
        if kind == "filter":
            return _do_filter(cfg, chain, cache_root)
        if kind == "inspect":
            tmp = cfg.tmp_dir or (
                str(self._cache.isa_path("")) if self._cache else None)
            if tmp and tmp.endswith("/"):
                tmp = tmp[:-1]
            return _do_inspect(cfg, chain, tmp)
        if kind == "score":
            return _do_score(cfg, chain)
        if kind == "describe":
            score_res = chain if isinstance(chain, ScoreResult) else \
                previous(ScoreResult)
            if score_res is None:
                raise ValueError("describe() has no ScoreResult to read")
            store_path = cfg.store or (
                str(self._cache.sentences_file) if self._cache else None)
            return _do_describe(cfg, score_res, llm, store_path)
        if kind == "download":
            insp = chain if isinstance(chain, InspectResult) else \
                previous(InspectResult)
            if insp is None:
                raise ValueError("download() has no InspectResult to read")
            files_cache = str(self.cache_root / "files") \
                if self.cache_root else None
            return _do_download(cfg, insp, files_cache_dir=files_cache)
        if kind == "export":
            insp = chain if isinstance(chain, InspectResult) else \
                previous(InspectResult)
            desc = previous(DescribeResult) \
                if previous(DescribeResult) is not None else None
            if insp is None:
                raise ValueError("export() has no InspectResult to read")
            return _do_export(cfg, insp, desc)
        raise ValueError(f"unknown step kind {kind!r}")


def json_dumps_sorted(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


# ──────────────────────────────────────────────────────────────
# Bodies — thin adapters over the existing function library
# ──────────────────────────────────────────────────────────────


def _do_search(cfg: SearchConfig) -> SearchResult:
    from metabo_search.searcher import profile_to_search_args, search_studies
    args = profile_to_search_args(cfg.profile) if cfg.profile else {}
    manual = {
        "filters": cfg.filters, "ms_filters": cfg.ms_filters,
        "organism": cfg.organism, "technique": cfg.technique,
        "sample_type": cfg.sample_type, "min_samples": cfg.min_samples,
        "min_raw_files": cfg.min_raw_files,
    }
    args = {**args, **{k: v for k, v in manual.items() if v is not None}}
    # merge raw filters lists
    if cfg.filters or args.get("filters"):
        base = list(args.get("filters") or []) + list(cfg.filters or [])
        args["filters"] = base
    cands = search_studies(
        query=cfg.query, page_size=cfg.page_size,
        max_results=cfg.max_results, sort_field=cfg.sort_field,
        sort_direction=cfg.sort_direction, **args)
    return SearchResult(candidates=cands, query=cfg.query, args_used=args)


def _carrier(r: Result) -> tuple[list[StudyCandidate], str]:
    if isinstance(r, SearchResult):
        return r.candidates, "shallow"
    if isinstance(r, FilterResult):
        return r.survivors, r.stage or ("shallow" if r.stage != "deep"
                                        else "deep")
    if isinstance(r, InspectResult):
        return r.candidates, "deep"
    raise TypeError(f"not a candidate carrier: {type(r).__name__}")


def _apply_predicate(p: Any, cands: list[StudyCandidate],
                     stage: str) -> tuple[list[StudyCandidate],
                                          list[tuple[StudyCandidate, str]],
                                          dict[str, float] | None]:
    from metabo_search.scorer import filter_by_maf, screen_candidates
    if isinstance(p, Screen):
        if p.profile is None:
            # identity: no criteria → no drops, no reorder; an explicit cap
            # still bounds the next stage (e.g. find_datasets' deep_inspect_top)
            cap = p.min_survivors
            if cap is None:
                return cands, [], None
            return (cands[:cap],
                    [(c, "kept beyond cap") for c in cands[cap:]], None)
        cap = p.min_survivors if p.min_survivors is not None else len(cands)
        scr = screen_candidates(cands, p.profile, min_survivors=cap)
        return scr.survivors, scr.dropped, scr.shallow_scores
    if isinstance(p, Maf):
        kept = filter_by_maf(cands, require_maf=p.require,
                             min_metabolites=p.min_metabolites)
        kept_ids = {c.study_id for c in kept}
        dropped = [(c, "no MAF" if p.require else "has MAF")
                   for c in cands if c.study_id not in kept_ids]
        return kept, dropped, None
    if isinstance(p, Custom):
        ent = _REGISTRY.get(p.name)
        if ent is None:
            raise ValueError(f"unknown predicate {p.name!r}")
        _, fn = ent
        out = fn(cands, p.params)
        if isinstance(out, tuple) and len(out) == 3:
            kept, dropped, order = out
        else:
            kept, dropped = out
            order = None
        return kept, list(dropped), order
    raise TypeError(f"not a predicate: {p!r}")


def _do_filter(cfg: FilterConfig, inp: Result,
               cache_root=None) -> FilterResult:
    cands, stage = _carrier(inp)
    survivors, all_dropped, order = cands, [], None
    for p in cfg.predicates:
        kept, dropped, o = _apply_predicate(p, survivors, stage)
        survivors, all_dropped = kept, all_dropped + dropped
        if o is not None:
            order = {**order, **o} if order else o
    return FilterResult(survivors=survivors, dropped=all_dropped,
                        order=order or {}, stage=stage)


def _do_inspect(cfg: InspectConfig, inp: Result,
                tmp_dir: str | None) -> InspectResult:
    from metabo_search.inspector import inspect_studies
    cands, _ = _carrier(inp)
    tmp = tmp_dir or cfg.tmp_dir
    deep = inspect_studies(cands, max_workers=cfg.workers,
                           tmp_dir=tmp,
                           parse_workers=cfg.parse_workers)
    isa_dirs = {}
    if tmp:
        from pathlib import Path
        base = Path(tmp)
        for c in deep:
            d = base / c.study_id / c.study_id
            if d.is_dir():
                isa_dirs[c.study_id] = str(d)
    return InspectResult(candidates=deep, isa_dirs=isa_dirs)


def _do_score(cfg: ScoreConfig, inp: Result) -> ScoreResult:
    from metabo_search.scorer import score_studies
    from metabo_search.summarizer import build_comparison_table
    cands, _ = _carrier(inp)
    if cfg.profile:
        ranked = score_studies(cands, cfg.profile)
    else:
        ranked = [ScoredCandidate(candidate=c, score=FitnessScore())
                  for c in cands]
    table = build_comparison_table(ranked, cfg.profile)
    return ScoreResult(ranked=ranked, table=table)


def _do_describe(cfg: DescribeConfig, score_res: ScoreResult,
                 llm: Callable | None, store_path: str | None):
    from metabo_search.sample_gen import (
        SampleSentencesStore, load_samples, prepare_samples, submit_samples)
    if llm is None:
        raise ValueError("describe() needs the LLM — run(llm=call_llm); "
                         "the library never calls a model itself")
    store = SampleSentencesStore(store_path) if store_path else None
    top_cands = score_res.ranked[: cfg.top]
    by_study: dict[str, list] = {}
    reused: dict[str, bool] = {}
    for sc in top_cands:
        sid = sc.study_id
        task = prepare_samples(sc.candidate, store, revision=cfg.revision)
        descs = load_samples(task)
        if descs is None:
            descs = submit_samples(task, llm(task.profile_prompt))
            reused[sid] = False
        else:
            reused[sid] = True
        by_study[sid] = descs
    return DescribeResult(by_study=by_study, revision=cfg.revision,
                          reused=reused)


def _do_download(cfg: DownloadConfig, insp: InspectResult,
                 files_cache_dir: str | None = None) -> DownloadResult:
    from metabo_search.downloader import (
        DownloadConfig as BodyConfig, download_data_files)
    all_downloaded: list[str] = []
    all_failed: list[str] = []
    total = 0
    for c in insp.candidates:
        body = BodyConfig(
            categories=cfg.categories, file_types=cfg.file_types,
            sample_names=cfg.sample_names, dest_dir=cfg.dest_dir,
            max_files=cfg.max_files, max_size_gb=cfg.max_size_gb,
            parallel_downloads=cfg.parallel,
            files_cache_dir=files_cache_dir or None)
        res = download_data_files(c, body)
        all_downloaded += [f.relative_path for f in res.downloaded]
        all_failed += list(res.failed)
        total += res.total_bytes
    return DownloadResult(dest_dir=cfg.dest_dir, downloaded=all_downloaded,
                          total_bytes=total, failed=all_failed)


def _do_export(cfg: ExportConfig, insp: InspectResult,
               desc: DescribeResult | None) -> ExportResult:
    from metabo_search.manifest import SampleManifest
    sentences_map = desc.by_study if desc and cfg.include_sentences else None
    manifest = SampleManifest.build(insp.candidates, sentences_map)
    manifest.export_csv(cfg.path)
    return ExportResult(path=cfg.path, rows=len(manifest.table_rows),
                        columns=manifest.table_columns)


# convenience constructors (avoid import-cycle with recipes)
def pipeline(*steps: Step, input: Result | None = None,
             cache_root=None) -> Pipeline:
    return Pipeline(*steps, input=input, cache_root=cache_root)