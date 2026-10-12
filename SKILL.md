---
name: metabo-search
description: Search MetaboLights and Metabolomics Workbench for metabolomics datasets from Python. Two independent, strictly typed clients — study search, point lookups, deep ISA/MAF inspection (MetaboLights), factors/analysis/metabolites (Workbench), selective downloads — plus a local DuckDB working set (search → ingest → SQL). Use when a researcher needs datasets matching an experimental setup, or wants to pull, ingest, and analyse study data.
compatibility: Python 3.11+; uv optional (falls back to python3 -m venv)
metadata:
  install: scripts/install.sh
  package: metabo_search
---

# metabo-search

Search two metabolomics repositories from Python. They are **not unified**:
MetaboLights and Metabolomics Workbench have different APIs, vocabularies, and
data models, and this library keeps them separate.

## Setup (run once, from the skill root)

```bash
./scripts/install.sh       # creates .venv-local, installs the package, links the skill
```

Use the venv python wrapper (portable, no hardcoded paths):

```bash
scripts/python -c "import metabo_search; print(metabo_search.__file__)"
```

> Never use the system/homebrew python. If `import metabo_search` resolves
> elsewhere, re-run `./scripts/install.sh`. Uninstall: `scripts/uninstall.sh`.

## MetaboLights

```python
from metabo_search import MetaboLights

ml = MetaboLights()

# Search the public study index. Facet filters run server-side; min_samples /
# min_raw_files are applied client-side (the API has no numeric filter).
hits = ml.search(
    "human blood plasma lipidomics",
    organisms=["Homo sapiens"],     # facet: organisms.term
    sample_types=["blood plasma"],  # facet: organismParts.term
    techniques=["LC-MS"],           # facet: assayTechniques.name
    min_samples=50,
    limit=25,
)

ml.study("MTBLS1")                  # one StudyHit
ml.studies(["MTBLS2", "MTBLS1"])    # input order preserved

# Deep data: download and parse ISA samples + MAF metabolites.
detail = ml.inspect(["MTBLS1"])[0]
detail.samples[0].organism          # "Homo sapiens"
detail.samples[0].organism_part     # "urine"
detail.metabolites[0].metabolite_identification
# inspect is best-effort: a study that failed after retries comes back with
# .error set (and empty data) instead of aborting the batch. Pass
# on_error="raise" to abort on the first failure.

ml.files("MTBLS1")                                   # recursive FILES/ listing
ml.download("MTBLS1", "derived", dest="data/", max_files=10)
```

MetaboLights vocabulary matters: use the index's facet values (`"blood plasma"`,
`"blood serum"`), not free variants.

## Metabolomics Workbench

```python
from metabo_search import Workbench

wb = Workbench()

# The Workbench has no server-side free-text search: the whole-index summary
# corpus is fetched and filtered locally (~90 s), then cached on disk
# (7-day TTL, ~/.cache/metabo-search) for later processes. Use
# Workbench(cache_dir=None) to disable, or wb.refresh_corpus() to force.
hits = wb.search("diabetes urine", limit=25)

wb.study("ST000001")
wb.samples("ST000001")       # parsed factor rows
wb.analyses("ST000001")      # MS/NMR analysis records
wb.metabolites("ST000001")   # empty means "not listed", not necessarily "none"

# inspect() bundles summary + samples + analyses + metabolites by default.
# Narrow the include_* flags to skip endpoints, and pass StudySummary objects
# (e.g. search hits) to reuse them instead of a /summary round trip per study.
wb.inspect(hits[:5], include_analyses=False, include_metabolites=False)
```

Workbench vocabulary differs: `species` (Latin name), `source` (e.g. `Blood`),
`analysis_type`. It is not translated to the MetaboLights vocabulary. The
Workbench SOURCE vocabulary has only `Blood` for biofluids, so serum vs plasma
must be checked per sample from the factor rows — and a bare `"plasma"` match
also catches `Seminal plasma`.

## Working set (DuckDB)

Search returns typed records; a `Workspace` is a local DuckDB file you ingest
only the ones you care about into, then query with SQL. The two repositories
are never merged — tables are prefixed `ml_*` / `wb_*`.

```python
from metabo_search import MetaboLights, Workbench, Workspace

ws = Workspace("project.duckdb")            # default: ./metabo.duckdb

ml = MetaboLights()
hits = ml.search("human blood plasma lipidomics", organisms=["Homo sapiens"], limit=100)
ml.ingest(hits, ws)                          # ml_study, ml_study_term, …
ml.ingest(ml.inspect(hits[:10]), ws)         # ml_sample, ml_metabolite, …

wb = Workbench()
wb.ingest(wb.search("diabetes", limit=50), ws)   # wb_study, …

ws.sql("select facet, count(*) n from ml_study_term group by 1 order by n desc")
ws.tables(); ws.schema()
```

- Ingestion is **idempotent** (upsert on natural keys): re-ingesting changes
  nothing; a `StudyDetail` after a `StudyHit` adds deeper rows, no duplicates.
- Every call returns an `IngestReport` (table → row count) and writes an
  `ml_ingest_run` / `wb_ingest_run` audit row. `ml.ingest` / `wb.ingest`
  delegate to `metabolights.ingest.write` / `workbench.ingest.write`.
- Errored `StudyDetail`s (best-effort inspect) are skipped, not written, and
  reported in `IngestReport.skipped`.
- `ws.sql()` returns DuckDB's own relation: `.fetchall()`, `.pl()`, `.df()`
  work; dataframe backends are optional, not dependencies.

## Notes

- **Offline by default in tests**: pass an `httpx` transport to either client.
- **Strictly typed**: pydantic models; `pyright` strict; `py.typed` shipped.
- Ingestion schema and steps: `docs/duckdb-ingestion-plan.md`.
