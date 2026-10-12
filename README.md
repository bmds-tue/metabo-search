# metabo-search

Search metabolomics datasets from Python, ingest the results you care about into
a local [DuckDB](https://duckdb.org), and analyse them with SQL.

It talks to two repositories — [MetaboLights](https://www.ebi.ac.uk/metabolights)
and [Metabolomics Workbench](https://www.metabolomicsworkbench.org) — through
two **independent, strictly typed clients**. They are not unified: the two
repositories have different APIs, vocabularies, and data models.

> **Status: rebuild in progress.** This branch is replacing an earlier attempt
> that forced both repositories behind one shared model and pipeline. The API
> shown below is the target; see [`docs/rebuild-plan.md`](docs/rebuild-plan.md)
> and [`docs/api-design.md`](docs/api-design.md).

## Why the rebuild

MetaboLights and Metabolomics Workbench disagree on almost everything: how you
search them, what organism and tissue names they use, how they expose
metabolites, and whether raw files are downloadable over HTTPS. Putting them
behind one object model produced silent wrong answers and endless special-casing.

The new approach keeps them separate and puts the join in the database: search
each web API, ingest only the results you are interested in, analyse, and fetch
more on demand. **The repositories are never mirrored.**

## Install

```bash
uv pip install -e .        # or: ./scripts/install.sh
```

## Examples

### Search MetaboLights and ingest the hits

```python
from metabo_search import MetaboLights, Workspace

ws = Workspace("project.duckdb")          # default: ./metabo.duckdb
ml = MetaboLights()

hits = ml.search(
    "human blood plasma lipidomics",
    organisms=["Homo sapiens"],
    sample_types=["blood plasma"],
    min_samples=50,
    limit=100,
)
ml.ingest(hits, ws)                        # only these studies land in DuckDB

ws.sql("select term, count(*) n from ml_study_term "
       "where facet = 'organism' group by 1 order by n desc")
```

### Go deeper on the studies you picked

```python
detail = ml.inspect(["MTBLS1", "MTBLS2"])  # download + parse ISA and MAF
ml.ingest(detail, ws)                      # adds ml_sample / ml_maf_*

ws.sql("select metabolite_name, formula from ml_maf_metabolite "
       "where study_id = 'MTBLS1' limit 20")
```

### Workbench, same loop

```python
from metabo_search import Workbench, Workspace

ws = Workspace("project.duckdb")
wb = Workbench()

wb.ingest(wb.search("diabetes", min_samples=10, limit=50), ws)
wb.ingest(wb.inspect(["ST000001"]), ws)

ws.sql("select metabolite_name from wb_metabolite limit 20")
```

### Inspect what is in the workspace

```python
ws.tables()        # table names
ws.schema()        # the full DDL
ws.export("select * from ml_study", "studies.parquet")
```

## Documentation

- [`docs/api-design.md`](docs/api-design.md) — the public API (target).
- [`docs/rebuild-plan.md`](docs/rebuild-plan.md) — architecture, phases, and the
  step-by-step execution plan.

## Disclaimer

Work in progress — no stability guarantees. Please open an issue if you find one.
