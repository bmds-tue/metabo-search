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
uv pip install -e .        # or: ./scripts/install.sh
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

# The Workbench has no server-side free-text search: the whole-index corpus
# is fetched and filtered locally (~90 s), then cached on disk (7-day TTL,
# ~/.cache/metabo-search) so later processes skip the fetch. Pass
# cache_dir=None to disable, or call wb.refresh_corpus() to force a refresh.
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

Ingestion is **idempotent** (upsert on natural keys): re-ingesting the same
object changes nothing, and a `StudyDetail` after a `StudyHit` adds the deeper
rows without duplicating the shallow ones. Every call returns an
`IngestReport` (table → row count) and appends an `ml_ingest_run` /
`wb_ingest_run` audit row. `ml.ingest` / `wb.ingest` delegate to
`metabolights.ingest.write` / `workbench.ingest.write`.

`ws.sql()` returns DuckDB's own relation, so `.fetchall()`, `.pl()`, `.df()`
work as usual. `pyarrow` is a dependency (it powers bulk ingestion); the polars
and pandas backends for `.pl()`/`.df()` remain optional.

## Documentation

- [`docs/api-design.md`](docs/api-design.md) — the target public API.
- [`docs/rebuild-plan.md`](docs/rebuild-plan.md) — architecture and plan.
- [`docs/duckdb-ingestion-plan.md`](docs/duckdb-ingestion-plan.md) — the
  ingestion schema and steps.

## Disclaimer

Work in progress — no stability guarantees. Please open an issue if you find one.
