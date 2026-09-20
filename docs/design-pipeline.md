# metabo-search pipeline design — typed steps over candidate data

Status: design (pre-implementation). Applies to `src/metabo_search/core/`.

## The idea in one sentence

A pipeline is an ordered list of **typed steps**; a step is `Config → Result`, named by what it does to the data; step N's output type is step N+1's input type; **the cache is the state** (steps + configs recorded, unchanged steps skip on restart); you may enter at any point by supplying a result as the pipeline's input.

## Design principles

1. **Steps are verbs, results are nouns.** Step kinds say what happens to the candidate set: `search` (fetch), `filter` (drop), `inspect` (enrich), `score` (measure+rank), and the annexes `describe` (annotate), `download` (copy), `export` (serialize). No "Spec" anywhere.
2. **Typed boundaries.** Every step declares an input type and output type. Compatibility is checked at plan time, before any network. Errors are concrete: "score needs InspectResult — add inspect() before it, or pass the result via pipeline(input=...)".
3. **Empty config = identity.** Contract knobs (page_size, workers, top) may have defaults; decision knobs (drops, truncation, fetching) default to off. Empty config must never silently modify your data. Exceptions are explicit and few:
   - `search` — needs a query (there is no sane fetch without one).
   - `inspect` — transformation is its job (shallow → deep).
   - `download` — requires ≥1 constraint (a footgun otherwise; it errors). Only `dest_dir` defaults.
4. **Filtering is one general step, not a bucket of special steps.** `maf(...)`, shallow screening, and future predicates are all *predicates* of the single `filter` step; a predicate declares which result type it applies to, and misplacement is a plan-time error.
5. **Opinionated numbers live in recipes, not steps.** `quick_probe`, `quick_discovery`, `full_report`, `harvest` encode caps (screen 10, describe top 3). Steps stay honest.
6. **Everything is plain data; the LLM is injected at run.** Configs serialize (dataclasses, like `models.py`); custom predicates are registered by name so configs stay JSON-plain and cacheable. The only LLM step is `describe`, and its `llm` callable is passed to `run(llm=...)`, never stored in the plan.

---

## The core type

```
Step(kind, config, cache, print)
  - input_type / output_type      derived from kind  (the contract)
  - body(kind)                    existing library function as adapter
  - config                        dataclass, per-kind fields (below)
  - cache  = CacheOpts(enabled=None, ttl)    None = inherit pipeline default
  - print  = PrintOpts(top=10, detail=False) default fmt() for this result
```

Result contract (every output type has exactly four things):

- `to_json() / from_json()` — serialize the fields the next step reads; `_raw_api_result` junk excluded; `Custom` predicate fns live in the registry, not the JSON.
- `digest()` — compact identity form (study ids + stage state); the cache-key basis.
- `fmt(detail=False)` — compact print: top rows, one line per study, counts.
- Nothing else.

---

## The flow (what happens to the data)

```
search ─► filter ─► inspect ─► score            main chain: shallow → survivors → deep → ranked
              ▲                  │
         predicates        describe/export      annexes: hang off any prior result,
              │             download            never mutate the main flow
         (screen, maf, custom)
```

---

## Configs & results (the classes)

All configs are dataclasses in the style of `models.py` (pyproject already
serdes-friendly; pydantic unnecessary). ⛔ = required, no default.

### search — fetch candidates

```python
@dataclass
class SearchConfig:
    query: str                                # ⛔  the one mandatory answer
    profile: RequirementProfile | None = None # derive server filters via profile_to_search_args
    page_size: int = 100
    max_results: int = 200
    filters: list[dict] | None = None         # raw API filters (merged with profile-derived)
    ms_filters: dict | None = None
    sort_field: str | None = None
    sort_direction: str = "desc"
    organism: str | list[str] | None = None   # legacy shortcuts (override profile)
    technique: str | list[str] | None = None
    sample_type: str | list[str] | None = None
    min_samples: int | None = None
    min_raw_files: int | None = None

@dataclass
class SearchResult:
    candidates: list[StudyCandidate]
    query: str
    args_used: dict[str, Any]                 # what the API actually received
```

### filter — drop by predicates

```python
@dataclass
class FilterConfig:
    predicates: list[Predicate]               # ⛔  ≥1 required

@dataclass
class FilterResult:
    survivors: list[StudyCandidate]           # ordered (screen) or input order (maf)
    dropped: list[tuple[StudyCandidate, str]] # (candidate, reason)
    order: dict[str, float]                   # soft scores behind the ordering
```

### inspect — enrich shallow → deep

```python
@dataclass
class InspectConfig:
    workers: int = 10
    tmp_dir: str | None = None                # None → cache-root /isa (or temp if no cache)

@dataclass
class InspectResult:
    candidates: list[StudyCandidate]          # deep (ISA parsed)
    isa_dirs: dict[str, str]                  # study_id → local ISA dir (best-effort)
```

### score — measure + rank

```python
@dataclass
class ScoreConfig:
    profile: RequirementProfile | None = None # None → neutral pass-through
    columns: list[str] | None = None          # comparison-table columns (default set)

@dataclass
class ScoreResult:
    ranked: list[ScoredCandidate]
    table: ComparisonReport
```

### describe — annotate (annex)

```python
@dataclass
class DescribeConfig:
    top: int = 3                              # ranked studies from the latest ScoreResult
    revision: int = 0
    store: str | None = None                  # None → cache-root /sentences.json

@dataclass
class DescribeResult:
    by_study: dict[str, list[SampleDescription]]
    revision: int
    reused: dict[str, bool]                   # study_id → came from store (hit)
```

### download — copy files (annex)

```python
@dataclass
class DownloadConfig:
    dest_dir: str = "."                       # default = cwd
    categories: list[str] | None = None       # "raw" | "derived" | "other"
    file_types: list[str] | None = None       # e.g. [".mzml", ".raw"]
    sample_names: list[str] | None = None
    max_files: int | None = None
    max_size_gb: float | None = None
    parallel: int = 4
    # validation: ≥1 of (categories, file_types, sample_names,
    #                    max_files, max_size_gb) required — else plan-time error

@dataclass
class DownloadResult:
    dest_dir: str
    downloaded: list[str]                     # relative paths
    total_bytes: int
    failed: list[str]
```

### export — serialize (annex)

```python
@dataclass
class ExportConfig:
    path: str = "manifest.csv"
    include_sentences: bool = True            # needs a describe result before export
    with_traceability: bool = True

@dataclass
class ExportResult:
    path: str
    rows: int
    columns: list[str]
```

### Predicates (the granularity of `filter`)

```python
@dataclass
class Screen:                                # applies to: SearchResult
    profile: RequirementProfile | None = None
    min_survivors: int | None = None         # None ⇒ never truncate (identity)

@dataclass
class Maf:                                   # applies to: InspectResult
    require: bool = True
    min_metabolites: int | None = None
    # pure in-memory: inspect already parsed metabolite_count / maf_files_parsed

# custom — registered by name so configs stay plain data (cacheable):
#   register_predicate("year_after", applies_to=InspectResult, fn=...)
@dataclass
class Custom:
    name: str
    params: dict = field(default_factory=dict)
```

Placement is part of the type contract: `maf(...)` inside a `filter` that sits on
a `SearchResult` is a plan-time error ("maf applies to InspectResult — place this
filter after inspect()"). Two filters, each at its stage:

```python
p = pipeline(
    search("urine alzheimer"),
    filter(screen(profile=profile, min_survivors=10)),
    inspect(),
    filter(maf(require=True, min_metabolites=200)),
    score(profile=profile),
)
```

---

## Caching — steps and their configs are the state

```python
p = pipeline(search("urine alzheimer"), filter(screen(profile=p)), inspect(), score(profile=p))
p = p.cache(".pipeline_cache")
```

Layout (cache root owns everything):

```
.pipeline_cache/
├── plan.json            # last run: [{kind, config, key, created_at, ttl}]
├── results/<key>.json   # per-step result payloads
├── isa/<MTBLS>/…        # persistent ISA dirs (inspect's tmp_dir when unset)
└── sentences.json       # describe's LLM store (revision-keyed, eternal)
```

Restart semantics — exactly your rule:

- `run()` reads `plan.json`; **each step compares its (kind, config) against the stored entry. Unchanged → skip execution and replay `<key>.json`. Changed → miss → execute + store + rewrite `plan.json`.**
- The key is automatic: `sha(kind + config_json + input_result.digest())`; a changed input (different prior result) is therefore a miss too — the suffix re-runs, the prefix replays.
- TTL puts time on the cache (the DB doesn't change often → generous): `search` 7d (index moves), `inspect` 30d (studies publish once), `describe` eternal (revision-keyed; wording rounds free), the rest off. Stale → re-run + refresh.
- `p.cache(None)` = all off. `CacheOpts(enabled=None, ttl)` per step overrides (None = inherit).

## Run — the cache is the continue-point

```python
r = p.run()                    # ordered, typed mapping: r["score"], r.score, r.keys()

# Continue = warm cache. Same pipeline again → pure JSON replay, zero network.
# Stop anywhere = slice the steps ("until score"):
pipeline(*p.steps[:4]).run()
# Extend = list concat; the common prefix replays warm, the suffix executes:
p2 = p.extend(filter(maf(min_metabolites=500)))
r2 = p2.run()

# Continue from a result — any pipeline can take the previous result as input:
p3 = pipeline(input=r["search"],
              filter(screen(profile=pp, min_survivors=5)),
              inspect(), score(profile=pp))
# (pipeline(input=...) → step0's input type must equal the input's type)

# Fully offline:
deep = InspectResult(candidates=[load_study_from_isa(sid, d) for sid, d in dirs])
r4 = pipeline(input=deep, score(profile=profile)).run()
```

No run handles, no `until`, no `resume`. `run(llm=...)` injects the single LLM
dependency — only `describe` reads it. `pipeline(...).validate()` runs before
`.run()`: input-type chains, predicate applicability, download constraints.

### Staged: iterate the search first, then go deep

The natural workflow — narrow the profile cheaply, then invest in depth:

```python
probe = pipeline(search("urine alzheimer"), filter(screen(profile=p, min_survivors=10)))

for tighten in iterations:                    # stage 1: profile narrowing, no deep work
    r = probe.extend(...).run() if changed else probe.run()
    print(r["filter"].fmt())                  # survivors + dropped reasons

deep = probe.extend(inspect(), score(profile=p))     # stage 2: once the profile is settled
r2 = deep.run()                                     # prefix replays warm
```

`pipeline(input=...)` exists for *explicit* handoffs (saved result from a previous
session, another pipeline's output, offline candidates) — the normal staged loop
doesn't need it, warm replay already is continuation.

---

## Recipes — opinionated mini-pipelines, same type, nothing implicit

```python
quick_probe(query, profile)     → pipeline(search(q),
                                     filter(screen(p, min_survivors=10)))        [stage 1]
quick_discovery(query, profile) → quick_probe + inspect() + score(p)             [stage 2]
full_report (query, profile, top=3)     = quick_discovery + describe(top)
harvest     (query, profile, cfg)       = full_report + download + export
```

Each is one factory returning a `Pipeline`; sliceable, extendable, cacheable like
any other. `find_datasets(...)` becomes `quick_discovery(...).run()` for
back-compat until SKILL.md moves to the pipe syntax.

---

## Refactor plan (minimal)

The new API is **adapter + result/cache layers only**. Zero rewrites of parsing,
scoring, sample-gen, download, manifest logic. All existing functions stay
available (they *are* the step bodies — the `import metabo_search` surface keeps
every current name).

```
src/metabo_search/
├── core/                 <-- NEW (~600-800 lines total)
│   ├── __init__.py       pipeline(), step factories, recipes
│   ├── steps.py          Step type, kind registry, configs, validation
│   ├── results.py        8 result types: to_json/from_json/digest/fmt
│   ├── cache.py          plan.json + result store, keys+TTL, cache-root layout
│   └── recipes.py        quick_probe/discovery, full_report, harvest
├── searcher/inspector/scorer/sample_gen/downloader/manifest...  UNTOUCHED
└── workflow.py           find_datasets → shim over quick_discovery (only legacy edit)
```

Kind → existing body (adapters, no new logic):

| step | body it calls |
|---|---|
| `search` | `search_studies(query, ..., **profile_to_search_args(profile))` |
| `filter(screen(...))` | `screen_candidates(cands, profile, min_survivors)` — exists |
| `filter(maf(...))` | `filter_by_maf(cands, require_maf, min_metabolites)` — exists |
| `filter(custom)` | registered predicate fn — the only genuinely new body |
| `inspect` | `inspect_studies(cands, max_workers, tmp_dir=cache/isa)` |
| `score` | `score_studies(...)` + `build_comparison_table(...)` |
| `describe` | `prepare_samples` / store-check / `submit_samples` (llm injected) |
| `download` | `list_data_files` + `download_data_files` (+ `DownloadResult` wrapped) |
| `export` | `SampleManifest.build` + write |

Result types are thin wrappers (`ScoreResult` wraps `ScoredCandidate`/
`ComparisonReport`; `InspectResult` wraps `StudyCandidate` + isa dirs). The only
new serialization code is the 8 result types — `StudyCandidate` serialization
excludes `_raw_api_result`. `digest()` keys off id + stage state.

Phases (tests-first):

1. **Baseline** — existing suite green, frozen.
2. **results.py** — round-trip + digest-stability tests. No behavior change.
3. **steps.py** — kinds, configs (identity defaults), validation refusals
   (score-before-inspect, maf placement, empty download).
4. **cache.py** — plan.json write/compare, hit/miss, TTL expiry, warm replay
   (**bodies monkeypatched to raise ⇒ assert no network**), per-step override.
5. **fold + recipes** — determinism test: new pipeline == old `find_datasets` on
   identical inputs; `pipeline(input=...)` midstream/offline.
6. **shim + docs** — `find_datasets` → `quick_discovery(...).run()` unwrap; existing
   tests stay green; SKILL.md moves to pipe syntax (old API table marked legacy).

Touched: `+4` new files, `1` legacy edit (`workflow.py`). Everything else
untouched, all public functions remain importable.

---

## Migration map (existing → new)

| today | becomes |
|---|---|
| `find_datasets(...)` | `quick_discovery(...).run()` (shim keeps it working) |
| `screen_candidates(cands, profile)` | `filter(screen(profile=...))` |
| `filter_by_maf(cands, ...)` | `filter(maf(...))` |
| `search_studies / inspect_studies / score_studies` | step bodies, still importable |
| `analyze_maf_files / render_maf_summary` | standalone library utility (not a step) |
| `prepare_samples / load_samples / submit_samples / revise_samples` | `describe(...)` bodies |
| `SampleSentencesStore` | `describe`'s backing store (revision-keyed) |
| `SampleTask` | internal to the describe step |

## Decisions (locked)

1. `download`: `dest_dir` defaults to `"."` (cwd); **≥1 constraint still required** (validation error, plan-time).
2. `export` consumes latest InspectResult implicitly (+ DescribeResult if one ran first).
3. `filter(screen(...))` stays explicit — recipes add it; no auto-insert.
4. Cache TTLs: search 7d · inspect 30d · describe eternal (revision-keyed) · rest off.
5. `pipeline(input=...)` for explicit midstream/offline entry; normal staging uses warm replay + `extend`.