# metabo-search

Search metabolomics datasets from Python, then analyse them.

It talks to two repositories — [MetaboLights](https://www.ebi.ac.uk/metabolights)
and [Metabolomics Workbench](https://www.metabolomicsworkbench.org) — through
two **independent, strictly typed clients**. They are not unified: the two
repositories have different APIs, vocabularies, and data models.

> **Status: rebuild in progress.** This branch (`refactor/clients`) delivers the
> two clients. The next branch adds a local DuckDB working set: search the web
> APIs, ingest only the results you care about, analyse, and fetch more on
> demand. See [`docs/api-design.md`](docs/api-design.md) and
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

## Documentation

- [`docs/api-design.md`](docs/api-design.md) — the target public API.
- [`docs/rebuild-plan.md`](docs/rebuild-plan.md) — architecture and plan.

## Disclaimer

Work in progress — no stability guarantees. Please open an issue if you find one.
