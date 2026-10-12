# Public API design — search first, ingest what you care about, analyse

Status: proposal (decisions locked below).

## 1. The loop

The web APIs are for **searching**. DuckDB is a **working set** you build from
those searches, analyse, and extend.

```
   search (web API) ──► typed results ──► ml.ingest(hits, ws) ──► DuckDB
        ▲                                                          │
        │                                                          ▼
        └──── inspect deeper / search again ──── analyse (SQL) ◄────┘
                   ml.ingest(detail, ws)
```

We do **not** mirror the repositories into DuckDB, and we never bulk-ingest a
whole index. You search, you ingest the hits you care about, you analyse, and
you fetch more only when you want to.

## 2. Sources stay separate

MetaboLights and Workbench have different APIs, vocabularies, and models. They
are never unified — not in clients, models, tables, or queries. A `Workspace`
may hold both side by side in separate tables, but there is no merged view.

```
metabo_search
├── workspace.py     # Workspace: a dumb DuckDB handle (no source knowledge)
├── metabolights/    # client · models · ingest (schema + mapping) · queries
└── workbench/       # client · models · ingest (schema + mapping) · queries
```

## 3. The loop in code

```python
from metabo_search import MetaboLights, Workspace

ws = Workspace("my_project.duckdb")      # default: ./metabo.duckdb
ml = MetaboLights()                      # no arguments required

# 1. search the web API
hits = ml.search("human blood plasma lipidomics",
                 organisms=["Homo sapiens"],
                 sample_types=["blood plasma"],
                 min_samples=50, limit=100)
print(hits.fmt())                        # hits is a list subclass; hits.df() too

# 2. ingest exactly those hits
ml.ingest(hits, ws)

# 3. analyse (the tables are yours — query them however you like)
ws.sql("select term, count(*) n from ml_study_term "
       "where facet = 'organism' group by 1 order by n desc")
ws.tables()                              # what exists
ws.schema()                              # DDL for all of it

# 4. get more: deeper per-study data for the ones you picked
detail = ml.inspect(["MTBLS1", "MTBLS2"])   # download + parse ISA/MAF
ml.ingest(detail, ws)                        # adds ml_sample / ml_maf_*
ws.sql("select * from ml_maf_metabolite where study_id = 'MTBLS1' limit 20")

# 5. or search again and add more
ml.ingest(ml.search("human serum NMR", min_samples=20, limit=50), ws)
```

Workbench side, same loop:

```python
from metabo_search import Workbench, Workspace

ws = Workspace("my_project.duckdb")
wb = Workbench()

wb.ingest(wb.search("diabetes", min_samples=10, limit=50), ws)
wb.ingest(wb.inspect(["ST000001", "ST000042"]), ws)
ws.sql("select * from wb_metabolite where study_id = 'ST000001' limit 20")
```

## 4. Ingestion architecture

**Rule: sources depend on the workspace; the workspace never depends on
sources.** Ingestion is a per-source module because the schema is per-source.

```
metabo_search/
├── workspace.py              # Workspace (connection, write(batch), sql, tables, schema)
├── metabolights/
│   ├── models.py             # StudyHit, StudyDetail, ...
│   ├── ingest.py             # DDL + batch(obj) -> IngestBatch + write(ws, obj)
│   └── ...
└── workbench/
    ├── models.py
    └── ingest.py
```

Each `ingest.py` owns three things that belong together:

1. the DDL for that source's tables,
2. a **pure** mapping from that source's pydantic models → table rows,
3. the write against the workspace.

```python
# metabolights/ingest.py
def batch(obj: StudyHit | StudyDetail) -> IngestBatch:
    """Models → rows. Pure: no DB, no network. Unit-tested directly."""

def write(ws: Workspace, obj) -> IngestReport:
    """Ensure DDL, then ws.write(batch(obj)). Idempotent upsert."""
```

```python
# workspace.py  (generic — no source imports)
@dataclass(frozen=True)
class TableRows:
    name: str
    rows: list[dict]
    keys: tuple[str, ...]          # natural key for upsert

@dataclass(frozen=True)
class IngestBatch:
    ddl: str
    tables: list[TableRows]

class Workspace:
    def write(self, batch: IngestBatch) -> IngestReport: ...
    def sql(self, query: str, params=None) -> Table: ...
    def tables(self) -> list[str]: ...
    def schema(self, table: str | None = None) -> str: ...
    def export(self, query: str, dest, *, format="parquet") -> Path: ...
```

`ml.ingest(hits, ws)` is the ergonomic entry point; it delegates to
`metabolights.ingest.write(ws, hits)`, which is also callable directly:

```python
from metabo_search.metabolights import ingest
ingest.write(ws, hits)
```

`Workspace.write` is **idempotent** (upsert on natural keys) and
**incremental**: re-adding the same hits changes nothing; adding `StudyDetail`
enriches rows already ingested from `StudyHit`.

**Why not `ws.add(obj)`:** it forces `Workspace` to map types → tables, i.e. to
know every source; adding a source would edit `Workspace`. A registry avoids the
edit but adds import-order magic. Keeping ingestion in the source module keeps
schema + mapping + models together and `Workspace` dumb.

## 5. Transparency (nothing is quietly lost)

1. **Total mapping, enforced by tests.** A contract test asserts that every field
   the client models is written to a column or a child table. Upstream drift is
   a failing test, not a silent drop.
2. **`IngestReport`** from every ingest call: tables touched + row counts.
3. **`ws.tables()` / `ws.schema()`** so the user always sees what exists and can
   query it directly.
4. **`ml_ingest_run` / `wb_ingest_run`** rows record what each `ingest` added
   (source, table, row count, timestamp) — an auditable trail inside the DB.
5. **No `raw_*` tables for now.** They are a replay/audit safety net only; add
   them later if re-normalization becomes a real need (additive, no API change).

## 6. Clients (core scope)

Only what the loop needs. Everything else is deferred (§9).

### MetaboLights

```python
ml.search(query="", *, organisms=None, sample_types=None, techniques=None,
          min_samples=None, min_raw_files=None, sort=None,
          limit=25) -> Results[StudyHit]
ml.study(study_id) -> StudyHit
ml.studies(study_ids) -> Results[StudyHit]
ml.inspect(study_ids, *, workers=8) -> Results[StudyDetail]   # ISA + MAF, parsed
ml.files(study_id, *, recursive=True) -> Results[StudyFile]
ml.download(study_id, selection="all", *, dest=".", max_files=None,
            max_bytes=None) -> DownloadReport
```

### Workbench

```python
wb.search(query="", *, min_samples=None, limit=25) -> Results[StudySummary]
wb.study(study_id) -> StudySummary
wb.samples(study_id) -> Results[SampleRow]
wb.analyses(study_id) -> Results[AnalysisRecord]
wb.metabolites(study_id) -> Results[MetaboliteRow] | MetaboliteList
wb.inspect(study_ids, *, workers=8) -> Results[StudyDetail]
```

`wb.search` uses the workbench's whole-index summary as its search index (there
is no server-side free-text search); only matching studies are returned and
ingested. The corpus itself is never ingested.

## 7. Tables written by ingest

Per source, no shared tables. One workspace file may hold both.

| Ingested object | Tables |
|---|---|
| `metabolights.StudyHit` | `ml_study`, `ml_study_term`, `ml_study_factor_pair`, `ml_study_lineage`, `ml_assay`, `ml_assay_technique`, `ml_publication`, `ml_protocol`, `ml_contact`, `ml_submitter` |
| `metabolights.StudyDetail` | `ml_sample`, `ml_sample_factor`, `ml_sample_file`, `ml_maf_metabolite`, `ml_maf_abundance`, `ml_file` |
| `metabolights.StudyFile` | `ml_file` |
| `workbench.StudySummary` | `wb_study`, `wb_disease`, `wb_source`, `wb_species` |
| `workbench.StudyDetail` | `wb_sample`, `wb_sample_factor`, `wb_analysis`, `wb_metabolite` |

## 8. `Workspace` and return types

```python
Workspace(path: str | Path = "metabo.duckdb")   # cwd by default; a path may be passed
```

- `Workspace` knows only tables/rows/SQL. It imports no source package.
- `Results[T]` is a **`list` subclass**: it behaves exactly like a list
  (`hits[0]`, `len`, slicing, iteration) and adds `.df()`, `.fmt()`, `.ids()`.
- `Table` is an Arrow-backed SQL result: `.records()`, `.df()`, `.arrow()`, `.fmt()`.
- Reports are typed: `IngestReport`, `DownloadReport`.

## 8.1 Model & code conventions

- pydantic v2 for every request/response; `Literal`/`Enum` for closed sets;
  discriminated unions for search clauses; constraints (`ge`, `le`, `pattern`,
  `min_length`) declared, not checked in code.
- Response records `frozen=True`; camelCase aliases via `Field(alias=...)`;
  snake_case column names for DuckDB.
- `py.typed`; full annotations; no `Any` in public signatures; **`pyright`
  strict** (no mypy); `ruff` for lint/format.
- Google-style docstrings on every public method (endpoint, raw shape, returns,
  raises, target tables) + inline comments where a step is non-obvious.
- Assert pre/post-conditions the type signature cannot express.
- Keep it simple: no clever dispatch or metaprogramming. If something needs to
  be fancy to manage, the design is wrong.

## 9. Deferred (special features)

Not built until the core loop is solid and tested:

- MetaboLights: advanced clause search, compound index, ontology, ORCID,
  statistics, search/export, raw ES.
- Workbench: `metstat`, `datatable`, `mwtab`, maps as first-class queries.
- Cross-source anything.
- Per-sample sentences (dropped).

Models and methods are structured so these can be added without changing the
core (`search` / `inspect` / `ingest` / `sql`).

## 10. Shared infrastructure (no domain semantics)

- `transport.HttpClient` — pooling, retries, `Retry-After`, rate limits,
  timeouts, raw sink, error mapping.
- `Results[T]`, `Table`.
- One exception hierarchy (`NotFound`, `NotIngested`, `APIError`,
  `RateLimited`, `TransientError`, `ClientError`, `WarehouseError`).

## 11. Defaults

| Setting | MetaboLights | Workbench |
|---|---|---|
| Base URL | `.../metabolights/ws3` | `.../metabolomicsworkbench.org/rest` |
| Mirror | `ftp.ebi.ac.uk/.../studies/public` | — |
| HTTP cache | on, per-endpoint TTL | on, per-endpoint TTL |
| Retries / timeout | 4, backoff+jitter / 30 s | 4 / 30 s (180 s for the corpus) |
| Rate limit | 5 req/s | 5 req/s (1 req/s corpus) |
| `limit` | 25 | 25 |
| `inspect` workers | 8 | 8 |
| Workspace path | `./metabo.duckdb` (path may be passed) | same |

## 12. Decisions (locked)

1. `Workspace` name. ✅
2. Ingestion is a per-source module with a pure `batch()` + generic
   `Workspace.write()`; `ml.ingest(hits, ws)` is the primary call. ✅
3. `inspect()` returns a `StudyDetail` bundle (follows the API). ✅
4. `Results` is a `list` subclass. ✅
5. Workspace defaults to `./metabo.duckdb`, local, no env override; a path may
   be passed. ✅
6. No `raw_*` tables now; completeness + transparency instead. ✅
