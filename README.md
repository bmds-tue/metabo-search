# metabo-search

Search metabolomics datasets from Python, then analyse them.

It talks to two repositories — [MetaboLights](https://www.ebi.ac.uk/metabolights)
and [Metabolomics Workbench](https://www.metabolomicsworkbench.org) — through
two **independent, strictly typed clients**. They are not unified: the two
repositories have different APIs, vocabularies, and data models.

> Search the web APIs, ingest only the results you care about into a local
> DuckDB file, analyse with SQL, and fetch more on demand. The repositories are
> never mirrored. See [`docs/api-design.md`](docs/api-design.md) and
> [`docs/rebuild-plan.md`](docs/rebuild-plan.md).

## Why the rebuild

An earlier version forced both repositories behind one shared model and
pipeline. Their APIs disagree on almost everything — search, organism and
tissue vocabularies, metabolite handling, raw-file access — so the shared layer
produced silent wrong answers and endless special-casing. The new approach
keeps the clients separate; a database is the place to join them, not a Python
object model.

## Install

```bash
uv pip install -e .              # clients only: search, inspect, download
uv pip install -e ".[ingest]"    # + the DuckDB working set (duckdb, pyarrow)
# or: ./scripts/install.sh       # dev environment (includes ingest)
```

## MetaboLights

```python
from metabo_search import MetaboLights

ml = MetaboLights()

# search the public study index (facet filters server-side)
hits = ml.search(
    "human blood plasma lipidomics",
    organisms=["Homo sapiens"],
    sample_types=["blood plasma"],
    techniques=["LC-MS"],
    min_samples=50,
    limit=25,
)
print(hits.ids())

# point lookups
study = ml.study("MTBLS1")
many = ml.studies(["MTBLS2", "MTBLS1"])       # input order preserved

# deep data: download and parse ISA samples + MAF metabolites
# (include_metabolites=False does a samples-only pass, skipping MAF downloads)
detail = ml.inspect(["MTBLS1"])[0]
print(len(detail.samples), "samples", len(detail.metabolites), "metabolites")
print(detail.samples[0].organism, detail.samples[0].organism_part)

# list and download data files
files = ml.files("MTBLS1")
report = ml.download("MTBLS1", "derived", dest="data/", max_files=10)
```

## Metabolomics Workbench

```python
from metabo_search import Workbench

wb = Workbench()

# wb.search() ranks a locally cached whole-index corpus by title-token overlap
# (slow first fetch, ~90 s, then cached 7 days in ~/.cache/metabo-search).
# For a keyed native lookup use wb.studies_by() instead — see below.
results = wb.search("diabetes urine", limit=25)
print(results.ids())

study = wb.study("ST000001")
samples = wb.samples("ST000001")           # parsed factor rows
analyses = wb.analyses("ST000001")
metabolites = wb.metabolites("ST000001")   # empty means "not listed"

# inspect() bundles summary + samples + analyses + metabolites by default.
# Narrow the include_* flags to skip endpoints, and pass StudySummary objects
# (e.g. search results) to reuse them instead of refetching:
detail = wb.inspect(results[:5], include_analyses=False, include_metabolites=False)[0]
```

### Finding studies: `search` vs `studies_by`

Two strategies. They return the same `Results[StudySummary]`, so both feed
`inspect()` and `ingest()`:

| | `search()` | `studies_by()` |
|---|---|---|
| Where | cached whole-index corpus | native REST API |
| Cost | ~90 s first call, then cached | instant, live |
| Criteria | title tokens + `min_samples` | exactly one input item |
| Matching | token overlap, ranked | the API's own semantics |
| Use when | broad discovery, several criteria | you have a key |

```python
wb.studies_by("refmet_name", "Glucose")   # by metabolite
wb.studies_by("institute", "Harvard")     # by institute
wb.studies_by("study_title", "Diabetes")  # API substring, not token overlap
```

`studies_by` takes one of `study_id`, `analysis_id`, `study_title`, `institute`,
`last_name`, `metabolite_id`, `kegg_id`, `refmet_name`. The API combines
nothing — no free-text-plus-filter, no organism or disease filter — so intersect
locally, or filter the corpus from `all_studies()`:

```python
both = set(wb.studies_by("institute", "Harvard").ids()) & set(
    wb.studies_by("refmet_name", "Glucose").ids()
)
```

## Throttling and workers

Both repositories throttle aggressive clients. `inspect` defaults to
`workers=4`, the measured sweet spot for corpus-scale runs: more workers trip
server rate limits, and the client then spends its wall-clock in
`Retry-After`/exponential backoff — on a whole-corpus scan, 8 workers was ~60%
slower than 4. Raise `workers` only for small batches.

Throttling is not silent. `inspect` emits a `UserWarning` when it sees 429s or
failed studies, and `client.stats` reports cumulative attempts, throttled
responses, and time spent in backoff (`client.reset_stats()` zeroes it).

## Working set (DuckDB)

A `Workspace` is a local DuckDB file. Ingest the results you care about, then
query them with SQL. Per-source tables (`ml_*` / `wb_*`) are never merged.

```python
from metabo_search import MetaboLights, Workbench, Workspace

ws = Workspace("project.duckdb")            # default: ./metabo.duckdb

ml = MetaboLights()
hits = ml.search("human blood plasma lipidomics", organisms=["Homo sapiens"], limit=100)
ml.ingest(hits, ws)                          # study-level tables (ml_study, …)
ml.ingest(ml.inspect(hits[:10]), ws)         # deep: samples + MAF metabolites

wb = Workbench()
wb.ingest(wb.search("diabetes", limit=50), ws)   # wb_study, …

ws.sql("select facet, count(*) n from ml_study_term group by 1 order by n desc")
ws.tables()
ws.schema()
```

Ingestion is **idempotent** (upsert on natural keys) and **upsert-only**:
re-ingesting the same object changes nothing, a `StudyDetail` after a
`StudyHit` adds the deeper rows without duplicating the shallow ones, and rows
removed upstream are never pruned. Every call returns an
`IngestReport` (table → row count) and appends an `ml_ingest_run` /
`wb_ingest_run` audit row. `ml.ingest` / `wb.ingest` delegate to
`metabolights.ingest.write` / `workbench.ingest.write`.

`ws.sql()` returns DuckDB's own relation, so `.fetchall()`, `.pl()`, `.df()`
work as usual. `Workspace` and `client.ingest()` need the optional `ingest`
extra (`duckdb` + `pyarrow`), which powers bulk ingestion; the polars and pandas
backends for `.pl()`/`.df()` remain optional. Importing the clients never pulls
in the ingest stack, and a missing extra raises a message naming it.

## Documentation

- [`CHANGELOG.md`](CHANGELOG.md) — release notes.
- [`docs/api-design.md`](docs/api-design.md) — the target public API.
- [`docs/rebuild-plan.md`](docs/rebuild-plan.md) — architecture and plan.
- [`docs/duckdb-ingestion-plan.md`](docs/duckdb-ingestion-plan.md) — the
  ingestion schema and steps.
- [`docs/ingestion-hardening-plan.md`](docs/ingestion-hardening-plan.md) — the
  fetch/ingest hardening record.
- [`docs/ci.md`](docs/ci.md) — CI tiers, the live test sample, and the promises
  asserted.

## Disclaimer

Work in progress — no stability guarantees. Please open an issue if you find one.

## License

MIT — see [`LICENSE`](LICENSE).
